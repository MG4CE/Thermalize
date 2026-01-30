"""
ESC/POS protocol implementation for thermal printers.
Supports both USB and Bluetooth connections.
"""

import logging
import time
from typing import Optional
from PIL import Image # type: ignore

from .usb import USBConnection
from .bluetooth import BluetoothConnection
from .exceptions import PrinterConnectionError

try:
    from escpos.printer import Usb, Serial as EscposSerial # type: ignore
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False

logger = logging.getLogger(__name__)


class ESCPOSPrinter:
    """ESC/POS protocol printer implementation."""
    
    def __init__(self, config: dict):
        """
        Initialize ESC/POS printer.
        
        Args:
            config: Printer configuration dictionary
        """
        if not ESCPOS_AVAILABLE:
            raise ImportError("ESC/POS library not available. Install with: pip install python-escpos")
        
        self.config = config
        self.usb_connection = None
        self.bluetooth_connection = None
        self.connection_type = None
        self.retry_attempts = config.get('retry_attempts', 3)
    
    def connect_usb(self, vendor_id: Optional[int] = None, product_id: Optional[int] = None) -> bool:
        """
        Connect to USB printer.
        
        Args:
            vendor_id: USB vendor ID (optional)
            product_id: USB product ID (optional)
            
        Returns:
            True if connection successful
        """
        try:
            self.usb_connection = USBConnection(self.config)
            self.usb_connection.connect(vendor_id, product_id)
            self.connection_type = 'usb'
            logger.info("[ESC/POS] Connected via USB")
            return True
        except Exception as e:
            logger.error(f"[ESC/POS] USB connection failed: {e}")
            self.usb_connection = None
            return False
    
    def connect_bluetooth(self, mac_address: Optional[str] = None, port: Optional[int] = None) -> bool:
        """
        Connect to Bluetooth printer.
        
        Args:
            mac_address: Bluetooth MAC address (optional)
            port: RFCOMM port (optional)
            
        Returns:
            True if connection successful
        """
        try:
            self.bluetooth_connection = BluetoothConnection(self.config)
            self.bluetooth_connection.connect(mac_address, port, protocol='escpos')
            self.connection_type = 'bluetooth'
            logger.info("[ESC/POS] Connected via Bluetooth")
            return True
        except Exception as e:
            logger.error(f"[ESC/POS] Bluetooth connection failed: {e}")
            self.bluetooth_connection = None
            return False
    
    def disconnect(self):
        """Disconnect from printer."""
        if self.usb_connection:
            self.usb_connection.disconnect()
            self.usb_connection = None
        
        if self.bluetooth_connection:
            self.bluetooth_connection.disconnect()
            self.bluetooth_connection = None
        
        self.connection_type = None
        logger.info("[ESC/POS] Disconnected")
    
    def is_connected(self) -> bool:
        """
        Check if printer is connected.
        
        Returns:
            True if connected
        """
        if self.usb_connection and self.usb_connection.is_connected():
            return True
        if self.bluetooth_connection and self.bluetooth_connection.is_connected():
            return True
        return False
    
    def _get_printer_object(self):
        """
        Get the underlying printer object.
        
        Returns:
            The escpos printer object
            
        Raises:
            PrinterConnectionError: If not connected
        """
        if self.usb_connection and self.usb_connection.is_connected():
            return self.usb_connection.get_printer()
        elif self.bluetooth_connection and self.bluetooth_connection.is_connected():
            return self.bluetooth_connection.get_connection()
        else:
            raise PrinterConnectionError("Printer not connected")
    
    def verify_connection(self, printer_obj) -> bool:
        """
        Verify that the printer is actually connected and responding.
        
        Args:
            printer_obj: The printer object to verify
            
        Returns:
            True if printer responds
        """
        try:
            # Try to query printer status
            if hasattr(printer_obj, '_raw'):
                printer_obj._raw(b'\x10\x04\x01')  # DLE EOT n (query printer status)
            else:
                # Fallback: try to send initialization command
                printer_obj._raw(b'\x1b\x40')  # ESC @ (initialize printer)
            
            logger.debug("[ESC/POS] Printer verification successful")
            return True
            
        except Exception as e:
            error_msg = str(e)
            # Some printers have endpoint issues but still work
            if 'endpoint' in error_msg.lower() or 'invalid endpoint' in error_msg.lower():
                logger.debug(f"[ESC/POS] Verification skipped (endpoint issue, but device accessible): {e}")
                return True
            
            logger.debug(f"[ESC/POS] Verification failed: {e}")
            # Currently returning True to ignore verification failures
            return False
    
    def print_image(self, image_path: str, auto_reconnect: bool = True) -> bool:
        """
        Print an image to the thermal printer.
        
        Args:
            image_path: Path to processed image file
            auto_reconnect: Whether to automatically reconnect on failure
            
        Returns:
            True if print successful
        """
        for attempt in range(self.retry_attempts if auto_reconnect else 1):
            if not self.is_connected():
                if auto_reconnect and attempt < self.retry_attempts - 1:
                    logger.warning(f"[ESC/POS] Printer not connected. Reconnect attempt {attempt+1}/{self.retry_attempts}")
                    time.sleep(1)
                    continue
                else:
                    logger.error("[ESC/POS] Printer not connected")
                    return False
            
            try:
                # Load image
                img = Image.open(image_path)
                
                # Ensure image is in the correct format (1-bit black and white)
                if img.mode != '1':
                    img = img.convert('1')
                
                # Get printer object
                printer = self._get_printer_object()
                
                # Print image
                printer.image(img)
                
                # Longer wait to allow printer to cool between operations
                time.sleep(0.5)
                
                # Feed paper once before cutting
                printer.text('\n')
                
                # Additional cooling time
                time.sleep(1)
                
                # Cut paper
                printer.cut()
                
                logger.info(f"[ESC/POS] Successfully printed image: {image_path}")
                
                # Auto-disconnect Bluetooth printer after successful print
                if self.connection_type == 'bluetooth':
                    logger.info("[ESC/POS] Auto-disconnecting Bluetooth printer")
                    time.sleep(1)
                    self.disconnect()
                
                return True
                
            except Exception as e:
                logger.error(f"[ESC/POS] Print attempt {attempt+1} failed: {e}")
                
                if auto_reconnect and attempt < self.retry_attempts - 1:
                    logger.info(f"[ESC/POS] Retrying in 2 seconds...")
                    time.sleep(2)
                    # Mark as disconnected to trigger reconnect
                    if self.usb_connection:
                        self.usb_connection.printer = None
                    if self.bluetooth_connection:
                        self.bluetooth_connection.serial_connection = None
                    continue
        
        logger.error("[ESC/POS] Failed to print after all retry attempts")
        return False
    
    def test_print(self) -> bool:
        """
        Print a test pattern.
        
        Returns:
            True if test print successful
        """
        if not self.is_connected():
            logger.error("[ESC/POS] Printer not connected")
            return False
        
        try:
            printer = self._get_printer_object()
            
            # Center align and bold
            printer.set(align='center', bold=True)
            printer.text('Thermal Printer Test\n')
            
            # Left align and normal weight
            printer.set(align='left', bold=False)
            printer.text('='*32 + '\n')
            printer.text('Status: OK\n')
            printer.text('Protocol: ESC/POS\n')
            printer.text('Width: 83mm (600px @ 203 DPI)\n')
            printer.text('='*32 + '\n')
            printer.text('\n\n\n')
            printer.cut()
            
            logger.info("[ESC/POS] Test print successful")
            
            # Auto-disconnect Bluetooth to free up printer
            if self.connection_type == 'bluetooth':
                logger.info("[ESC/POS] Auto-disconnecting Bluetooth printer")
                time.sleep(1)
                self.disconnect()
            
            return True
            
        except Exception as e:
            logger.error(f"[ESC/POS] Test print failed: {e}")
            return False
    
    def get_status(self) -> dict:
        """
        Get printer status.
        
        Returns:
            Dictionary with status information
        """
        status = {
            'connected': self.is_connected(),
            'connection_type': self.connection_type,
            'protocol': 'escpos'
        }
        
        if self.connection_type == 'usb' and self.usb_connection:
            status['vendor_id'] = hex(self.usb_connection.vendor_id) if self.usb_connection.vendor_id else None
            status['product_id'] = hex(self.usb_connection.product_id) if self.usb_connection.product_id else None
        elif self.connection_type == 'bluetooth' and self.bluetooth_connection:
            status['mac_address'] = self.bluetooth_connection.mac_address
        
        return status
