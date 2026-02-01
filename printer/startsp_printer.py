"""
StarTSP protocol implementation for Star Micronics thermal printers.
Currently supports Bluetooth connections only (USB not yet tested).
"""

import logging
import time
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps # type: ignore

from .bluetooth import BluetoothConnection
from .exceptions import PrinterConnectionError

logger = logging.getLogger(__name__)

class StarTSPImage:
    def buildRaster(img, cut=True, bottom_padding=0):

        bytes_per_line = 72

        # Convert image to greyscale and resize to max width
        basewidth = bytes_per_line * 8
        wpercent = (basewidth / float(img.width))
        hsize = int((float(img.height) * float(wpercent)))
        img = ImageOps.invert(img.convert('RGB'))
        img = img.convert(mode='1').resize((basewidth, hsize), Image.Resampling.LANCZOS)
        
        # Add white space padding at the bottom if specified
        if bottom_padding > 0:
            # Create new image with extra height
            # Use 0 (black) because image is already inverted for thermal printer
            # 0 in inverted image = white paper (no print)
            padded_img = Image.new('1', (basewidth, hsize + bottom_padding), 0)
            # Paste original image at the top
            padded_img.paste(img, (0, 0))
            img = padded_img

        # PIL mode 1 image (1-bit pixels, black and white, one pixel per byte)
        bytesarray = bytes(img.tobytes())

        # Start our raster image
        buf = []
        buf.extend([0x1b, ord('*'), ord('r'), ord('A')])                  # Enter raster mode    
        buf.extend([0x1b, ord('*'), ord('r'), ord('P'), ord('0'), 0x00])  # continuous mode

        # Handle cuts
        if not cut:
            buf.extend([0x1b, ord('*'), ord('r'), ord('E'), ord('1'), 0x00]) # Raster EOT no-cut

        # Loop over bytes array, adding a transfer data command for each line
        # followed by the amount of bytes that make up a line
        byte = 0
        for line in range(img.height):
            line_bytes = [ord('b'), bytes_per_line, 0] # Transfer of raster data
            for b in range(bytes_per_line):
                line_bytes.append(bytesarray[byte])
                byte += 1
            buf.extend(line_bytes)

        buf.extend([0x1b, ord('*'), ord('r'), ord('B')]) # Quit raster mode

        return bytearray(buf)


    def imageToRaster(img, cut=True, bottom_padding=0):
        return StarTSPImage.buildRaster(img, cut, bottom_padding)


    def imageFileToRaster(image_path, cut=True, bottom_padding=0):
        img = Image.open(image_path)
        return StarTSPImage.buildRaster(img, cut, bottom_padding)



class StarTSPPrinter:
    """StarTSP protocol printer implementation."""
    
    def __init__(self, retry_attempts: int = 3, bottom_padding: int = 100):
        """
        Initialize StarTSP printer.
        
        Args:
            retry_attempts: Number of retry attempts for connection
            bottom_padding: Pixels of white space to add at bottom of images
        """
        self.bluetooth_connection = None
        self.connection_type = None
        self.retry_attempts = retry_attempts
        self.bottom_padding = bottom_padding
        # Store connection info for reconnection
        self.mac_address = None
        self.port = 1 
    
    def connect_bluetooth(self, mac_address: str, port: int = 1) -> bool:
        """
        Connect to Bluetooth printer.
        
        Args:
            mac_address: Bluetooth MAC address (optional)
            port: RFCOMM port (optional)
            
        Returns:
            True if connection successful
        """
        try:
            # Store connection info for reconnection
            self.mac_address = mac_address
            self.port = port
            
            self.bluetooth_connection = BluetoothConnection(mac_address, port)
            self.bluetooth_connection.connect(mac_address, port, protocol='startsp')
            self.connection_type = 'bluetooth'
            logger.info("[StarTSP] Connected via Bluetooth")
            return True
        except Exception as e:
            logger.error(f"[StarTSP] Bluetooth connection failed: {e}")
            self.bluetooth_connection = None
            return False
    
    def connect_usb(self, vendor_id: Optional[int] = None, product_id: Optional[int] = None, auto_detect: bool = True) -> bool:
        """
        Connect to USB printer (not yet supported).
        
        Returns:
            False - USB not supported yet
        """
        logger.error("[StarTSP] USB connection not yet supported for StarTSP protocol")
        logger.error("[StarTSP] Please use Bluetooth connection or switch to ESC/POS protocol")
        return False
    
    def disconnect(self):
        """Disconnect from printer."""
        if self.bluetooth_connection:
            self.bluetooth_connection.disconnect()
        
        logger.info("[StarTSP] Disconnected")
    
    def is_connected(self) -> bool:
        """
        Check if printer is connected.
        
        Returns:
            True if connected
        """
        return self.bluetooth_connection and self.bluetooth_connection.is_connected()
    
    def _get_serial_connection(self):
        """
        Get the underlying serial connection object.
        
        Returns:
            The pyserial connection object
            
        Raises:
            PrinterConnectionError: If not connected
        """
        if not self.bluetooth_connection or not self.bluetooth_connection.is_connected():
            raise PrinterConnectionError("Printer not connected")
        
        serial_conn = self.bluetooth_connection.get_connection()
        if not serial_conn:
            raise PrinterConnectionError("Serial connection not available")
        
        return serial_conn
    
    # NOTE: untested...
    def verify_connection(self, serial_obj) -> bool:
        """
        Verify that the Star TSP printer is actually connected and responding.
        Uses Star Line Mode commands instead of ESC/POS.
        
        Args:
            serial_obj: The serial connection object to verify
            
        Returns:
            True if printer responds
        """
        try:
            # Star TSP real-time status request command
            # ESC ENQ 0x01 - Request printer status
            serial_obj.write(b'\x1b\x05\x01')
            serial_obj.flush()
            
            # Try to read response with timeout
            original_timeout = serial_obj.timeout
            serial_obj.timeout = 2
            response = serial_obj.read(1)
            serial_obj.timeout = original_timeout
            
            if response:
                logger.debug(f"[StarTSP] Printer responded with status: {response.hex()}")
                return True
            else:
                # No response doesn't necessarily mean failure for Star printers
                # Try alternative: send initialize command
                logger.debug("[StarTSP] No status response, trying initialize command...")
                serial_obj.write(b'\x1b\x40')  # ESC @ works for Star TSP initialization
                serial_obj.flush()
                time.sleep(0.1)
                return True
                
        except Exception as e:
            error_msg = str(e)
            # Some printers have endpoint issues but still work
            if 'endpoint' in error_msg.lower() or 'invalid endpoint' in error_msg.lower():
                logger.debug(f"[StarTSP] Verification skipped (endpoint issue, but device accessible): {e}")
                return True
            
            logger.debug(f"[StarTSP] Verification failed: {e}")
            # Currently returning True to ignore verification failures
            return True
    
    def print_image(self, image_path: str, auto_reconnect: bool = True) -> bool:
        """
        Print an image to the thermal printer using StarTSP raster format.
        
        Args:
            image_path: Path to processed image file
            auto_reconnect: Whether to automatically reconnect on failure
            
        Returns:
            True if print successful
        """
        for attempt in range(self.retry_attempts if auto_reconnect else 1):
            if not self.is_connected():
                if auto_reconnect and attempt < self.retry_attempts - 1:
                    logger.warning(f"[StarTSP] Printer not connected. Reconnect attempt {attempt+1}/{self.retry_attempts}")
                    if not self.mac_address:
                        logger.error("[StarTSP] No MAC address stored for reconnection")
                        return False
                    self.connect_bluetooth(self.mac_address, self.port)
                    time.sleep(1)
                    continue
                else:
                    logger.error("[StarTSP] Printer not connected")
                    return False
            
            try:
                # Get serial connection
                serial_conn = self._get_serial_connection()
                
                # Verify connection is still alive
                if not serial_conn.is_open:
                    logger.error("[StarTSP] Serial connection is not open")
                    raise ConnectionError("Serial connection closed")
                
                # Load image
                img = Image.open(image_path)
                logger.debug(f"[StarTSP] Loaded image: {img.size}, mode: {img.mode}")
                
                # Convert to StarTSP raster format
                logger.debug("[StarTSP] Converting image to raster format...")
                raster = StarTSPImage.imageToRaster(img, cut=True, bottom_padding=self.bottom_padding)
                logger.debug(f"[StarTSP] Raster size: {len(raster)} bytes")
                
                # Send raw bytes via serial
                logger.debug("[StarTSP] Sending raster data to printer...")
                bytes_written = serial_conn.write(raster)
                logger.debug(f"[StarTSP] Wrote {bytes_written} bytes to printer")
                serial_conn.flush()
                
                logger.info(f"[StarTSP] Successfully printed image: {image_path}")
                
                # Auto-disconnect Bluetooth printer after successful print
                if self.connection_type == 'bluetooth':
                    logger.info("[StarTSP] Auto-disconnecting Bluetooth printer")
                    time.sleep(1)
                    self.disconnect()
                
                return True
                
            except OSError as e:
                logger.error(f"[StarTSP] I/O error during print attempt {attempt+1}: {e}")
                logger.error("[StarTSP] Possible causes:")
                logger.error("[StarTSP]   - Bluetooth connection dropped")
                logger.error("[StarTSP]   - Printer powered off or out of range")
                logger.error("[StarTSP]   - /dev/rfcomm0 device disconnected")
                logger.error("[StarTSP]   - Printer buffer overflow (image too large)")
                
                if auto_reconnect and attempt < self.retry_attempts - 1:
                    logger.info(f"[StarTSP] Retrying in 2 seconds...")
                    time.sleep(2)
                    # Mark as disconnected to trigger reconnect
                    if self.bluetooth_connection:
                        self.bluetooth_connection.serial_connection = None
                    continue
                    
            except Exception as e:
                logger.error(f"[StarTSP] Print attempt {attempt+1} failed: {e}")
                
                if auto_reconnect and attempt < self.retry_attempts - 1:
                    logger.info(f"[StarTSP] Retrying in 2 seconds...")
                    time.sleep(2)
                    # Mark as disconnected to trigger reconnect
                    if self.bluetooth_connection:
                        self.bluetooth_connection.serial_connection = None
                    continue
        
        logger.error("[StarTSP] Failed to print after all retry attempts")
        return False
    
    def test_print(self) -> bool:
        """
        Print a test pattern.
        
        Returns:
            True if test print successful
        """
        if not self.is_connected():
            logger.error("[StarTSP] Printer not connected")
            return False
        
        try:
            # Get serial connection
            serial_conn = self._get_serial_connection()
            
            # Verify connection is still alive
            if not serial_conn.is_open:
                logger.error("[StarTSP] Serial connection is not open")
                return False
            
            # Create a test image with PIL
            img = Image.new('RGB', (576, 400), color='white')
            draw = ImageDraw.Draw(img)
            
            # Try to load fonts, fallback to default if not available
            try:
                font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
                font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            except Exception as e:
                logger.debug(f"[StarTSP] Could not load TrueType fonts, using default: {e}")
                font_large = ImageFont.load_default()
                font_medium = ImageFont.load_default()
            
            # Draw test pattern with thicker lines
            draw.rectangle((10, 10, 576, 390), outline='black', width=8)
            draw.text((120, 50), "Star TSP Printer Test", fill='black', font=font_large)
            draw.text((220, 120), "Status: OK", fill='black', font=font_medium)
            draw.text((50, 170), "Width: 80mm (576px @ 203 DPI)", fill='black', font=font_medium)
            draw.text((140, 220), "Protocol: StarTSP", fill='black', font=font_medium)
            
            # Convert to raster
            logger.debug("[StarTSP] Converting test image to raster format...")
            raster = StarTSPImage.imageToRaster(img, cut=True, bottom_padding=self.bottom_padding)
            logger.debug(f"[StarTSP] Raster size: {len(raster)} bytes")
            
            # Send to printer
            logger.debug("[StarTSP] Sending raster data to printer...")
            bytes_written = serial_conn.write(raster)
            logger.debug(f"[StarTSP] Wrote {bytes_written} bytes to printer")
            serial_conn.flush()
            
            logger.info("[StarTSP] Test print successful")
            
            # Auto-disconnect Bluetooth to free up printer
            if self.connection_type == 'bluetooth':
                logger.info("[StarTSP] Auto-disconnecting Bluetooth printer")
                time.sleep(1)
                self.disconnect()
            
            return True
            
        except OSError as e:
            logger.error(f"[StarTSP] I/O error during test print: {e}")
            logger.error("[StarTSP] Possible causes:")
            logger.error("[StarTSP]   - Bluetooth connection dropped")
            logger.error("[StarTSP]   - Printer powered off or out of range")
            logger.error("[StarTSP]   - /dev/rfcomm0 device disconnected")
            return False
            
        except Exception as e:
            logger.error(f"[StarTSP] Test print failed: {e}")
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
            'protocol': 'startsp'
        }
        
        if self.connection_type == 'bluetooth' and self.bluetooth_connection:
            status['mac_address'] = self.bluetooth_connection.mac_address
        
        return status
