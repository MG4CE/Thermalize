"""
USB connection management for thermal printers.
Handles USB device detection, connection, and verification.
"""

import logging
from typing import Optional, Tuple

from .exceptions import USBConnectionError, PrinterNotFoundError

try:
    from escpos.printer import Usb # type: ignore
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False

logger = logging.getLogger(__name__)


class USBConnection:
    """Manages USB printer connections."""
    
    # Common thermal printer vendor/product IDs
    COMMON_PRINTER_IDS = [
        (0x0fe6, 0x811e),  # Gprinter GP-58
        (0x0416, 0x5011),  # Default common ID
        (0x04b8, 0x0e15),  # Epson
        (0x0dd4, 0x0205),  # Generic
        (0x1fc9, 0x2016),  # Generic
    ]
    
    def __init__(self, config: dict):
        """
        Initialize USB connection handler.
        
        Args:
            config: Printer configuration dictionary
        """
        self.config = config
        self.printer = None
        self.vendor_id = None
        self.product_id = None
    
    def detect_printer(self) -> Optional[Tuple[int, int]]:
        """
        Attempt to detect a connected USB thermal printer.
        
        Returns:
            Tuple of (vendor_id, product_id) if found, None otherwise
            
        Raises:
            USBConnectionError: If escpos library is not available
        """
        if not ESCPOS_AVAILABLE:
            raise USBConnectionError(
                "ESC/POS library not available. Install with: pip install python-escpos[usb]"
            )
        
        logger.info(f"[USB] Auto-detecting printer, trying {len(self.COMMON_PRINTER_IDS)} known IDs...")
        
        for vid, pid in self.COMMON_PRINTER_IDS:
            try:
                logger.debug(f"[USB] Trying VID: {hex(vid)}, PID: {hex(pid)}")
                
                # Try with specific endpoints first
                test_printer = Usb(vid, pid, in_ep=0x82, out_ep=0x03)
                logger.debug(f"[USB] Device opened with endpoints IN=0x82, OUT=0x03")
                
                if self._verify_connection(test_printer):
                    logger.info(f"[USB] Printer detected: VID={hex(vid)}, PID={hex(pid)}")
                    test_printer.close()
                    return (vid, pid)
                else:
                    test_printer.close()
                    
            except Exception as e:
                logger.debug(f"[USB] Endpoints 0x82/0x03 failed, trying auto-detect: {e}")
                try:
                    # Try with auto-detected endpoints
                    test_printer = Usb(vid, pid)
                    logger.debug(f"[USB] Device opened with auto-detect")
                    
                    if self._verify_connection(test_printer):
                        logger.info(f"[USB] Printer detected: VID={hex(vid)}, PID={hex(pid)}")
                        test_printer.close()
                        return (vid, pid)
                    else:
                        test_printer.close()
                        
                except Exception as e2:
                    logger.debug(f"[USB] Failed to connect to {hex(vid)}:{hex(pid)} - {type(e2).__name__}: {e2}")
                    continue
        
        logger.warning(f"[USB] No printer detected from {len(self.COMMON_PRINTER_IDS)} known IDs")
        return None
    
    def connect(self, vendor_id: Optional[int] = None, product_id: Optional[int] = None) -> bool:
        """
        Connect to USB printer.
        
        Args:
            vendor_id: USB vendor ID (if None, uses config or auto-detect)
            product_id: USB product ID (if None, uses config or auto-detect)
            
        Returns:
            True if connection successful
            
        Raises:
            USBConnectionError: If connection fails
            PrinterNotFoundError: If no printer is found
        """
        if not ESCPOS_AVAILABLE:
            raise USBConnectionError(
                "ESC/POS library not available. Install with: pip install python-escpos[usb]"
            )
        
        # Determine which IDs to use
        if vendor_id and product_id:
            vid, pid = vendor_id, product_id
            logger.info(f"[USB] Connecting to specified device: VID={hex(vid)}, PID={hex(pid)}")
        elif self.config.get('auto_detect', True):
            # Auto-detect printer
            detected = self.detect_printer()
            if not detected:
                raise PrinterNotFoundError("No USB printer found during auto-detection")
            vid, pid = detected
        else:
            # Use configured IDs
            vid = self.config.get('vendor_id')
            pid = self.config.get('product_id')
            
            if not vid or not pid:
                raise USBConnectionError(
                    "No vendor_id/product_id configured and auto_detect is disabled",
                    context={'vendor_id': vid, 'product_id': pid}
                )
            
            logger.info(f"[USB] Using configured IDs: VID={hex(vid)}, PID={hex(pid)}")
        
        # Attempt connection
        try:
            logger.debug(f"[USB] Opening device {hex(vid)}:{hex(pid)} with endpoints IN=0x82, OUT=0x03")
            test_printer = Usb(vid, pid, in_ep=0x82, out_ep=0x03)
            
            if not self._verify_connection(test_printer):
                test_printer.close()
                raise USBConnectionError(
                    f"Printer verification failed for {hex(vid)}:{hex(pid)}"
                )
            
            self.printer = test_printer
            self.vendor_id = vid
            self.product_id = pid
            logger.info(f"[USB] Successfully connected to printer: VID={hex(vid)}, PID={hex(pid)}")
            return True
            
        except Exception as e:
            logger.debug(f"[USB] Specific endpoints failed, trying auto-detect: {e}")
            try:
                # Fallback to auto-detect endpoints
                test_printer = Usb(vid, pid)
                
                if not self._verify_connection(test_printer):
                    test_printer.close()
                    raise USBConnectionError(
                        f"Printer verification failed for {hex(vid)}:{hex(pid)}"
                    )
                
                self.printer = test_printer
                self.vendor_id = vid
                self.product_id = pid
                logger.info(f"[USB] Successfully connected to printer: VID={hex(vid)}, PID={hex(pid)}")
                return True
                
            except Exception as e2:
                raise USBConnectionError(
                    f"Failed to connect to USB printer {hex(vid)}:{hex(pid)}",
                    context={'error': str(e2), 'vid': hex(vid), 'pid': hex(pid)}
                )
    
    def _verify_connection(self, printer_obj) -> bool:
        """
        Verify that the printer is actually connected and responding.
        
        Args:
            printer_obj: The USB printer object to verify
            
        Returns:
            True if printer responds, False otherwise
        """
        try:
            # Try to query printer status
            if hasattr(printer_obj, '_raw'):
                printer_obj._raw(b'\x10\x04\x01')  # DLE EOT n (query printer status)
            else:
                # Fallback: try to send initialization command
                printer_obj._raw(b'\x1b\x40')  # ESC @ (initialize printer)
            
            logger.debug("[USB] Printer verification successful")
            return True
            
        except Exception as e:
            error_msg = str(e)
            # Some printers have endpoint issues but still work
            if 'endpoint' in error_msg.lower() or 'invalid endpoint' in error_msg.lower():
                logger.debug(f"[USB] Printer verification skipped (endpoint issue, but device accessible): {e}")
                return True  # Device opened successfully, assume it works
            
            logger.debug(f"[USB] Printer verification failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from USB printer."""
        if self.printer:
            try:
                self.printer.close()
                logger.info("[USB] Printer disconnected")
            except RuntimeError as e:
                if 'usb library' in str(e).lower():
                    logger.debug(f"[USB] USB library not installed, skipping cleanup: {e}")
                else:
                    logger.debug(f"[USB] Error during disconnect: {e}")
            except Exception as e:
                logger.debug(f"[USB] Error during disconnect: {e}")
            finally:
                self.printer = None
                self.vendor_id = None
                self.product_id = None
    
    def is_connected(self) -> bool:
        """
        Check if printer is connected and device is still present.
        
        Returns:
            True if connected
        """
        if self.printer is None:
            return False
        
        # Verify the device is still physically present
        try:
            # Try to verify the device is accessible
            if not self._verify_connection(self.printer):
                logger.warning("[USB] Device no longer accessible, marking as disconnected")
                self.disconnect()
                return False
            return True
        except Exception as e:
            logger.warning(f"[USB] Connection check failed: {e}")
            self.disconnect()
            return False
    
    def get_printer(self):
        """
        Get the underlying printer object.
        
        Returns:
            The escpos Usb printer object
        """
        return self.printer
