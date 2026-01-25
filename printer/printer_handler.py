"""
Thermal printer handler using python-escpos and StarTSPImage libraries.
Manages printer connection and print operations.
"""

import json
import logging
import re
import time
import os
import serial # type: ignore
import subprocess

from typing import Optional, List, Dict
from PIL import Image, ImageDraw, ImageFont # type: ignore

try:
    from escpos.printer import Usb, Serial as EscposSerial # type: ignore
    from escpos import exceptions as escpos_exceptions # type: ignore
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False

try:
    import StarTSPImage # type: ignore
    STARTSP_AVAILABLE = True
except ImportError:
    STARTSP_AVAILABLE = False

try:
    import bluetooth # type: ignore
    BLUETOOTH_AVAILABLE = True
except ImportError:
    BLUETOOTH_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("pybluez2 not available. Bluetooth scanning will be disabled. Pairing via bluetoothctl still works.")


logger = logging.getLogger(__name__)


class PrinterHandler:
    """Handle thermal printer operations."""
    
    def __init__(self, config_path='config.json'):
        """Initialize printer handler with configuration."""
        try:
            with open(config_path, 'r') as f:
                self.config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise e
        
        self.printer = None
        self.startsp_serial = None
        self.is_connected = False
        self.connection_type = None  # 'usb', 'bluetooth', or None
        self.bluetooth_mac = None
        self.retry_attempts = self.config['printer'].get('retry_attempts', 3)
        self.protocol = self.config['printer'].get('protocol', 'escpos')
        self.simulation_mode = False
        
        logger.info("="*60)
        logger.info("PrinterHandler Initialization")
        logger.info(f"Protocol: {self.protocol}")
        logger.info(f"Connection type: {self.config['printer'].get('type', 'usb')}")
        logger.info(f"Bluetooth MAC: {self.config['printer'].get('bluetooth_mac', 'Not configured')}")
        logger.info("="*60)
        
        # Attempt to connect to printer
        logger.info("Attempting to connect to printer...")
        result = self.connect()
        logger.info(f"Connection attempt result: {result}, is_connected = {self.is_connected}")
        if not result:
            self.simulation_mode = True
            logger.warning("*" * 60)
            logger.warning("RUNNING IN SIMULATION MODE (NO PRINTER CONNECTED)")
            logger.warning("*" * 60)
        else:
            logger.info("*" * 60)
            logger.info(f"PRINTER CONNECTED SUCCESSFULLY via {self.connection_type}")
            logger.info("*" * 60)


    def _save_config(self):
        """Save current configuration to file."""
        try:
            with open('config.json', 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Configuration saved successfully")
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
    

    def _verify_connection(self, printer_obj) -> bool:
        """
        Verify that the printer is actually connected and responding (ESC/POS only).
        
        Args:
            printer_obj: The USB printer object to verify
            
        Returns:
            bool: True if printer responds, False otherwise
        """
        try:
            # Try to query printer status - this will fail if printer is not connected
            if hasattr(printer_obj, '_raw'):
                printer_obj._raw(b'\x10\x04\x01')  # DLE EOT n (query printer status)
            else:
                # Fallback: try to send initialization command
                printer_obj._raw(b'\x1b\x40')  # ESC @ (initialize printer)
            return True
        except Exception as e:
            error_msg = str(e)
            # Some printers have endpoint issues but still work - if we can open the device, it's probably OK
            if 'endpoint' in error_msg.lower() or 'invalid endpoint' in error_msg.lower():
                logger.info(f"Printer verification skipped (endpoint issue, but device is accessible): {e}")
                return True  # Device opened successfully, assume it works
            # The _raw() method will raise an exception if the device isn't responding
            logger.debug(f"Printer verification failed: {e}")
            return True #False lets ignore verification failures for now
    

    # NOTE: untested...
    def _verify_startsp_connection(self, serial_obj) -> bool:
        """
        Verify that the Star TSP printer is actually connected and responding.
        Uses Star Line Mode commands instead of ESC/POS.
        
        Args:
            serial_obj: The serial connection object to verify
            
        Returns:
            bool: True if printer responds, False otherwise
        """
        try:
            # Star TSP real-time status request command
            # ESC ENQ 0x01 - Request printer status
            serial_obj.write(b'\x1b\x05\x01')
            serial_obj.flush()
            
            # Try to read response with timeout
            serial_obj.timeout = 2
            response = serial_obj.read(1)
            
            if response:
                logger.debug(f"Star TSP printer responded with status: {response.hex()}")
                return True
            else:
                # No response doesn't necessarily mean failure for Star printers
                # Try alternative: send initialize command and check for errors
                logger.debug("No status response, trying initialize command...")
                serial_obj.write(b'\x1b\x40')  # ESC @ works for Star TSP initialization
                serial_obj.flush()
                time.sleep(0.1)
                return True
                
        except Exception as e:
            error_msg = str(e)
            # Some printers have endpoint issues but still work - if we can open the device, it's probably OK
            if 'endpoint' in error_msg.lower() or 'invalid endpoint' in error_msg.lower():
                logger.info(f"StarTSP printer verification skipped (endpoint issue, but device is accessible): {e}")
                return True  # Device opened successfully, assume it works
            logger.debug(f"Star TSP verification failed: {e}")
            return True #False lets ignore verification failures for now
    

    def connect(self) -> bool:
        """
        Connect to printer using configured connection type and protocol.
        
        Returns:
            bool: True if connection successful
        """
        try:
            printer_config = self.config['printer']
            conn_type = printer_config.get('type', 'usb')
            
            if conn_type == 'bluetooth':
                return self.connect_bluetooth()
            elif conn_type == 'usb':
                return self.connect_usb()
            elif conn_type == 'auto':
                # Try Bluetooth first, then USB
                logger.info("Auto-detect mode: trying Bluetooth first...")
                if self.connect_bluetooth():
                    return True

                logger.info("Bluetooth failed, trying USB...")
                return self.connect_usb()
            else:
                logger.error(f"Unknown connection type: {conn_type}")
                return False
                
        except Exception as e:
            logger.error(f"Error in connect(): {e}")
            return False
    

    def switch_protocol(self, new_protocol: str) -> bool:
        """
        Switch printer protocol at runtime.
        
        Args:
            new_protocol: 'escpos' or 'startsp'
            
        Returns:
            bool: True if switch successful
        """
        if new_protocol not in ['escpos', 'startsp']:
            logger.error(f"Invalid protocol: {new_protocol}")
            return False
        
        # Disconnect current connection
        if self.is_connected:
            logger.info(f"Disconnecting from {self.protocol} printer before protocol switch")
            time.sleep(1)  # Wait a moment before disconnect
            self.disconnect()
        
        # Update protocol
        old_protocol = self.protocol
        self.protocol = new_protocol
        
        # Update config file
        self.config['printer']['protocol'] = new_protocol
        try:
            with open('config.json', 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info(f"Protocol switched from {old_protocol} to {new_protocol}")
            return True
        except Exception as e:
            logger.error(f"Failed to save protocol to config: {e}")
            self.protocol = old_protocol  # Revert
            return False
    

    def connect_usb(self) -> bool:
        """
        Connect to USB thermal printer and verify connection.
        
        Returns:
            bool: True if connection successful and verified
        """
        # USB only supported for ESC/POS
        if self.protocol == 'startsp':
            # TODO: implement StarTSP USB connection and test it...
            logger.error("*" * 60)
            logger.error("USB CONNECTION NOT SUPPORTED FOR STARTSP PROTOCOL")
            logger.error("Please either:")
            logger.error("  1. Switch to 'bluetooth' connection type in config.json, OR")
            logger.error("  2. Switch to 'escpos' protocol in config.json")
            logger.error("*" * 60)
            return False
        
        if not ESCPOS_AVAILABLE:
            logger.error("ESC/POS not available. Cannot connect via USB.")
            logger.error("Install python-escpos with USB support: pip install python-escpos[usb]")
            return False
        
        try:
            printer_config = self.config['printer']
            
            if printer_config.get('auto_detect', True):
                # Try to auto-detect printer
                # Common thermal printer IDs
                common_ids = [
                    (0x0fe6, 0x811e),  # Gprinter GP-58
                    (0x0416, 0x5011),  # Default common ID
                    (0x04b8, 0x0e15),  # Epson
                    (0x0dd4, 0x0205),  # Generic
                    (0x1fc9, 0x2016),  # Generic
                ]
                
                logger.info(f"Auto-detecting USB printer, trying {len(common_ids)} known IDs...")
                
                for vid, pid in common_ids:
                    try:
                        logger.debug(f"Trying VID: {hex(vid)}, PID: {hex(pid)}")
                        # Try to open with auto-detected endpoints first
                        test_printer = Usb(vid, pid, in_ep=0x82, out_ep=0x03)
                        logger.debug(f"USB device opened with endpoints IN=0x82, OUT=0x03, verifying connection...")
                        
                        if self._verify_connection(test_printer):
                            self.printer = test_printer
                            self.is_connected = True
                            self.connection_type = 'usb'
                            logger.info(f"Printer connected (VID: {hex(vid)}, PID: {hex(pid)})")
                            return True
                        else:
                            logger.debug(f"Verification failed for {hex(vid)}:{hex(pid)}")
                            test_printer.close()
                    except Exception as e:
                        # If specific endpoints fail, try default (auto-detect)
                        logger.debug(f"Endpoints 0x82/0x03 failed, trying auto-detect: {e}")
                        try:
                            test_printer = Usb(vid, pid)
                            logger.debug(f"USB device opened with auto-detect, verifying connection...")
                            
                            if self._verify_connection(test_printer):
                                self.printer = test_printer
                                self.is_connected = True
                                self.connection_type = 'usb'
                                logger.info(f"Printer connected (VID: {hex(vid)}, PID: {hex(pid)})")
                                return True
                            else:
                                logger.debug(f"Verification failed for {hex(vid)}:{hex(pid)}")
                                test_printer.close()
                        except Exception as e2:
                            logger.debug(f"Failed to connect to {hex(vid)}:{hex(pid)} - {type(e2).__name__}: {e2}")
                            continue
                
                logger.warning(f"None of the {len(common_ids)} common printer IDs matched")
            else:
                # Use configured vendor/product IDs
                vid = printer_config.get('vendor_id')
                pid = printer_config.get('product_id')
                
                logger.info(f"Using configured VID: {hex(vid) if vid else 'None'}, PID: {hex(pid) if pid else 'None'}")
                
                if vid and pid:
                    try:
                        logger.debug(f"Opening USB device {hex(vid)}:{hex(pid)} with endpoints IN=0x82, OUT=0x03")
                        # Try with specific endpoints first
                        test_printer = Usb(vid, pid, in_ep=0x82, out_ep=0x03)
                        logger.debug(f"USB device opened, verifying connection...")
                        
                        if self._verify_connection(test_printer):
                            self.printer = test_printer
                            self.is_connected = True
                            self.connection_type = 'usb'
                            logger.info(f"Printer connected (VID: {hex(vid)}, PID: {hex(pid)})")
                            return True
                        else:
                            test_printer.close()
                    except Exception as e:
                        logger.debug(f"Specific endpoints failed, trying auto-detect: {e}")
                        try:
                            # Fallback to auto-detect
                            test_printer = Usb(vid, pid)
                            if self._verify_connection(test_printer):
                                self.printer = test_printer
                                self.is_connected = True
                                self.connection_type = 'usb'
                                logger.info(f"Printer connected (VID: {hex(vid)}, PID: {hex(pid)})")
                                return True
                            else:
                                test_printer.close()
                        except:
                            pass
            
            logger.warning("Could not connect to thermal printer")
            self.is_connected = False
            return False
            
        except Exception as e:
            logger.error(f"Error connecting to printer: {e}")
            self.is_connected = False
            return False
    

    def connect_bluetooth(self, mac_address: Optional[str] = None, port: int = 1) -> bool:
        """
        Connect to Bluetooth thermal printer.
        
        Args:
            mac_address: Bluetooth MAC address (e.g., "AA:BB:CC:DD:EE:FF")
                        If None, uses config value
            port: RFCOMM port (default: 1, Star TSP100III typically uses 1)
            
        Returns:
            bool: True if connection successful
        """
        try:
            # Get MAC from parameter or config
            mac = mac_address or self.config['printer'].get('bluetooth_mac')
            if not mac:
                logger.error("*" * 60)
                logger.error("NO BLUETOOTH MAC ADDRESS CONFIGURED")
                logger.error("Please set 'bluetooth_mac' in config.json")
                logger.error("Example: \"bluetooth_mac\": \"00:11:22:33:44:55\"")
                logger.error("*" * 60)
                return False
            
            # Get port from config if not specified
            if mac_address is None:  # Only use config port if using config MAC
                port = self.config['printer'].get('bluetooth_port', 1)
            
            # Validate MAC address format
            if not self._validate_mac_address(mac):
                logger.error(f"Invalid MAC address format: {mac}")
                return False
            
            # Check if device is paired at OS level, attempt pairing if not
            if not self.check_bluetooth_pairing(mac):
                logger.info(f"Device {mac} not paired. Running scan and attempting automatic pairing...")
                
                # Run a quick scan to make the device available for pairing
                logger.info("Scanning for Bluetooth devices...")
                try:
                    # Use echo to send commands to bluetoothctl
                    scan_cmd = f'echo -e "scan on\\nquit" | timeout 5 bluetoothctl'
                    subprocess.run(
                        scan_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=7
                    )
                    # Give it a moment to register devices
                    time.sleep(1)
                    logger.info("Bluetooth scan completed")
                except Exception as e:
                    logger.warning(f"Scan failed but continuing: {e}")
                
                if not self.pair_bluetooth_device(mac):
                    logger.error(f"Failed to pair device {mac}. Cannot proceed with connection.")
                    return False
            
            # Attempt connection using rfcomm bind
            logger.info(f"Attempting Bluetooth connection to: {mac}:{port}")
            
            # First, try to bind the Bluetooth device to an RFCOMM device node
            try:                
                # Check if rfcomm0 is already bound
                rfcomm_device = '/dev/rfcomm0'
                
                # Try to release any existing binding first
                # NOTE: probably not a good idea to do this automatically in production
                try:
                    subprocess.run(['sudo', 'rfcomm', 'release', '0'], 
                                 capture_output=True, timeout=5, check=False)
                except:
                    pass
                
                # Bind the Bluetooth MAC to rfcomm0
                logger.info(f"Binding {mac} to {rfcomm_device}...")
                result = subprocess.run(
                    ['sudo', 'rfcomm', 'bind', '0', mac, str(port)],
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                
                if result.returncode != 0:
                    logger.error(f"Failed to bind rfcomm device: {result.stderr}")
                    logger.error("Try manually: sudo rfcomm bind 0 {mac} {port}")
                    return False
                
                # Wait a moment for the device to be ready
                time.sleep(1)
                
                # Check if device exists
                if not os.path.exists(rfcomm_device):
                    logger.error(f"RFCOMM device {rfcomm_device} not created")
                    return False
                
                logger.info(f"Successfully bound to {rfcomm_device}")
                
                # Now connect based on protocol
                if self.protocol == 'startsp':
                    # For StarTSP, use pyserial directly
                    test_printer = serial.Serial(
                        rfcomm_device,
                        baudrate=9600,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=10
                    )
                    logger.info(f"StarTSP serial connection created over Bluetooth")
                else:
                    # For ESC/POS, use python-escpos Serial printer
                    if not ESCPOS_AVAILABLE:
                        logger.error("ESC/POS not available but protocol is set to escpos")
                        return False
                    
                    test_printer = EscposSerial(
                        devfile=rfcomm_device,
                        baudrate=9600,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=10
                    )
                    logger.info(f"ESC/POS Serial connection created over Bluetooth, verifying...")
                
            except Exception as e:
                logger.error(f"Failed to create Bluetooth connection: {type(e).__name__}: {e}")
                logger.error(f"Make sure the device {mac} is paired at OS level (bluetoothctl)")
                logger.error("And that rfcomm tools are installed: sudo apt-get install bluez")
                return False
            
            # Verify connection
            logger.info("Sending verification command to printer...")
            try:
                if self.protocol == 'startsp':
                    # Verify Star TSP printer with Star-specific commands
                    if self._verify_startsp_connection(test_printer):
                        self.startsp_serial = test_printer
                        self.is_connected = True
                        self.connection_type = 'bluetooth'
                        self.bluetooth_mac = mac
                        logger.info(f"StarTSP Bluetooth printer connected successfully: {mac}")
                        return True
                    else:
                        logger.error("Star TSP printer did not respond to verification command")
                        logger.error("Possible causes: printer powered off, out of range, or wrong RFCOMM port")
                        test_printer.close()
                        return False
                else:
                    # For ESC/POS, verify with status command
                    if self._verify_connection(test_printer):
                        self.printer = test_printer
                        self.is_connected = True
                        self.connection_type = 'bluetooth'
                        self.bluetooth_mac = mac
                        logger.info(f"ESC/POS Bluetooth printer connected successfully: {mac}")
                        return True
                    else:
                        logger.error("Bluetooth printer did not respond to verification command")
                        logger.error("Possible causes: printer powered off, out of range, or wrong RFCOMM port")
                        test_printer.close()
                        return False
            except Exception as e:
                logger.error(f"Connection verification failed: {type(e).__name__}: {e}")
                try:
                    test_printer.close()
                except:
                    pass
                return False
                
        except Exception as e:
            logger.error(f"Unexpected error in connect_bluetooth: {type(e).__name__}: {e}")
            logger.exception("Full traceback:")
            self.is_connected = False
            return False


    def _validate_mac_address(self, mac: str) -> bool:
        """Validate Bluetooth MAC address format."""
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        return bool(re.match(pattern, mac))


    def pair_bluetooth_device(self, mac: str, timeout: int = 30) -> bool:
        """
        Pair a Bluetooth device at OS level using bluetoothctl.
        
        Args:
            mac: MAC address to pair
            timeout: Maximum time to wait for pairing (seconds)
            
        Returns:
            bool: True if pairing successful
        """
        try:            
            # Validate MAC address format
            if not self._validate_mac_address(mac):
                logger.error(f"Invalid MAC address format: {mac}")
                return False
            
            logger.info(f"Attempting to pair with device {mac}...")
            
            # First, check if already paired
            if self.check_bluetooth_pairing(mac):
                logger.info(f"Device {mac} is already paired")
                return True
            
            # Try to pair using bluetoothctl
            logger.info(f"Running: bluetoothctl pair {mac}")
            result = subprocess.run(
                ['bluetoothctl', 'pair', mac],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            # Check if pairing was successful
            if result.returncode == 0 or 'Pairing successful' in result.stdout:
                logger.info(f"Successfully paired with device {mac}")
                
                # Also try to trust the device for auto-reconnection
                try:
                    subprocess.run(
                        ['bluetoothctl', 'trust', mac],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False
                    )
                    logger.info(f"Device {mac} marked as trusted")
                except:
                    pass
                
                return True
            else:
                logger.error(f"Failed to pair with device {mac}")
                logger.error(f"Output: {result.stdout}")
                logger.error(f"Error: {result.stderr}")
                logger.error("Make sure:")
                logger.error("  1. Bluetooth is enabled: bluetoothctl power on")
                logger.error("  2. Device is in pairing mode")
                logger.error("  3. Device is within range")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"Pairing timed out after {timeout} seconds")
            logger.error("Device may not be in pairing mode or out of range")
            return False
        except Exception as e:
            logger.error(f"Error during Bluetooth pairing: {e}")
            return False


    def check_bluetooth_pairing(self, mac: str) -> bool:
        """
        Check if a Bluetooth device is paired at OS level.
        
        Args:
            mac: MAC address to check
            
        Returns:
            bool: True if device appears to be paired
        """
        try:
            # Try to check with bluetoothctl
            result = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # If device info is returned, it's likely paired
            if result.returncode == 0 and 'Device' in result.stdout:
                logger.info(f"Device {mac} appears to be paired at OS level")
                return True
            else:
                logger.debug(f"Device {mac} not currently paired")
                return False
                
        except Exception as e:
            logger.debug(f"Could not check pairing status: {e}")
            return False  # Assume not paired if we can't check

    
    def scan_bluetooth_devices(self, timeout: int = 10) -> List[Dict]:
        """
        Scan for nearby Bluetooth devices.
        
        Args:
            timeout: Scan duration in seconds
            
        Returns:
            list: Devices with format [{"name": str, "mac": str, "class": int, "is_printer": bool, "is_tsp100": bool}]
        """
        if not BLUETOOTH_AVAILABLE:
            logger.error("pybluez2 not available. Cannot scan for Bluetooth devices.")
            logger.info("You can still pair devices using: bluetoothctl pair <MAC>")
            return []
        
        try:
            logger.info(f"Scanning for Bluetooth devices ({timeout}s)...")
            
            devices = bluetooth.discover_devices(
                duration=timeout,
                lookup_names=True,
                lookup_class=True,
                flush_cache=True
            )
            
            result = []
            for addr, name, dev_class in devices:
                # Filter for potential printers
                # Device class 0x1680 = Imaging/Printer
                is_printer = (dev_class & 0x1FFF) == 0x1680
                device_name = name or 'Unknown Device'
                
                result.append({
                    'mac': addr,
                    'name': device_name,
                    'class': dev_class,
                    'is_printer': is_printer,
                    'is_tsp100': 'TSP100' in device_name.upper()
                })
            
            logger.info(f"Found {len(result)} Bluetooth devices")
            return result
            
        except Exception as e:
            logger.error(f"Bluetooth scan failed: {e}")
            return []


    def disconnect(self):
        """Disconnect from printer."""
        # Handle ESC/POS printer
        if self.printer:
            try:
                # For Serial connections, explicitly close before cleanup
                if hasattr(self.printer, 'device') and hasattr(self.printer.device, 'close'):
                    self.printer.device.close()
                # Call the escpos close method (may try to flush again, but catch any errors)
                self.printer.close()
            except Exception as e:
                # Ignore errors during close (port may already be closed)
                logger.debug(f"Ignoring error during printer close: {e}")
            finally:
                self.printer = None
        
        # Handle StarTSP serial connection
        if self.startsp_serial:
            try:
                self.startsp_serial.close()
            except Exception as e:
                logger.debug(f"Ignoring error during StarTSP serial close: {e}")
            finally:
                self.startsp_serial = None
            
        # If Bluetooth connection, release the RFCOMM device
        if self.connection_type == 'bluetooth':
            try:
                logger.info("Releasing RFCOMM device...")
                subprocess.run(['sudo', 'rfcomm', 'release', '0'], 
                             capture_output=True, timeout=5, check=False)
            except Exception as e:
                logger.debug(f"Could not release RFCOMM: {e}")
        
        self.is_connected = False
        logger.info("Printer disconnected")


    def print_image(self, image_path: str) -> bool:
        """
        Print an image to the thermal printer with automatic retry.
        Routes to appropriate protocol handler.
        
        Args:
            image_path: Path to processed image file
            
        Returns:
            bool: True if print successful
        """
        if self.protocol == 'startsp':
            return self._print_image_startsp(image_path)
        else:
            return self._print_image_escpos(image_path)
    

    def _print_image_escpos(self, image_path: str) -> bool:
        """
        Print an image using ESC/POS protocol.
        
        Args:
            image_path: Path to processed image file
            
        Returns:
            bool: True if print successful
        """
        if not ESCPOS_AVAILABLE:
            logger.error("ESC/POS not available. Cannot print.")
            return False
        
        if self.simulation_mode:
            logger.info(f"Simulation: Would print image {image_path}")
            return True
        
        # Retry logic with automatic reconnection
        for attempt in range(self.retry_attempts):
            if not self.is_connected:
                logger.warning(f"Printer not connected. Reconnect attempt {attempt+1}/{self.retry_attempts}")
                if not self.connect():
                    if attempt < self.retry_attempts - 1:
                        time.sleep(1)  # Wait before retry
                        continue
                    else:
                        logger.error("Failed to reconnect after all retries")
                        return False
            
            try:
                # Load image
                img = Image.open(image_path)
                
                # Ensure image is in the correct format
                if img.mode != '1':
                    img = img.convert('1')
                
                # self.printer.text('\n')

                # Print image
                self.printer.image(img)
                
                # Feed paper after print
                # self.printer.text('\n')
                self.printer.cut()
                
                logger.info(f"Successfully printed image: {image_path}")
                
                # Auto-disconnect Bluetooth printer after successful print
                if self.connection_type == 'bluetooth':
                    logger.info("Auto-disconnecting Bluetooth printer after successful print")
                    time.sleep(1)  # Wait a moment before disconnect
                    self.disconnect()
                
                return True
                
            except Exception as e:
                logger.error(f"Print attempt {attempt+1} failed: {e}")
                self.is_connected = False
                
                if attempt < self.retry_attempts - 1:
                    time.sleep(2)
                    continue
        
        return False
    

    def _print_image_startsp(self, image_path: str) -> bool:
        """
        Print an image using StarTSP protocol (StarTSPImage library).
        
        Args:
            image_path: Path to processed image file
            
        Returns:
            bool: True if print successful
        """
        if self.simulation_mode:
            logger.info(f"Simulation: Would print image {image_path}")
            return True
    
        # Retry logic with automatic reconnection
        for attempt in range(self.retry_attempts):
            if not self.is_connected:
                logger.warning(f"Printer not connected. Reconnect attempt {attempt+1}/{self.retry_attempts}")
                if not self.connect():
                    if attempt < self.retry_attempts - 1:
                        time.sleep(1)  # Wait before retry
                        continue
                    else:
                        logger.error("Failed to reconnect after all retries")
                        return False
            
            try:
                # Verify connection is still alive
                if not self.startsp_serial or not self.startsp_serial.is_open:
                    logger.error("StarTSP serial connection is not open")
                    self.is_connected = False
                    raise ConnectionError("Serial connection closed")
                
                # Load image
                img = Image.open(image_path)
                logger.info(f"Loaded image: {img.size}, mode: {img.mode}")
                
                # Feed one line at the start
                self.startsp_serial.write(b'\n')
                self.startsp_serial.flush()
                
                # Convert to StarTSP raster format (handles resizing to 640px and dithering)
                logger.info("Converting image to StarTSP raster format...")
                raster = StarTSPImage.imageToRaster(img, cut=True)
                logger.info(f"Raster size: {len(raster)} bytes")
                
                # Send raw bytes via serial
                logger.info("Sending raster data to printer...")
                bytes_written = self.startsp_serial.write(raster)
                logger.info(f"Wrote {bytes_written} bytes to printer")
                self.startsp_serial.flush()
                
                logger.info(f"Successfully printed image via StarTSP: {image_path}")
                
                # Auto-disconnect Bluetooth printer after successful print
                if self.connection_type == 'bluetooth':
                    logger.info("Auto-disconnecting Bluetooth printer after successful print")
                    time.sleep(1)  # Wait a moment before disconnect
                    self.disconnect()
                
                return True
                
            except OSError as e:
                logger.error(f"I/O Error during StarTSP print attempt {attempt+1}: {e}")
                logger.error("Possible causes:")
                logger.error("  - Bluetooth connection dropped")
                logger.error("  - Printer powered off or out of range")
                logger.error("  - /dev/rfcomm0 device disconnected")
                logger.error("  - Printer buffer overflow (image too large)")
                self.is_connected = False
                
                if attempt < self.retry_attempts - 1:
                    logger.info(f"Retrying in 2 seconds...")
                    time.sleep(2)
                    continue
            except Exception as e:
                logger.error(f"StarTSP print attempt {attempt+1} failed: {e}")
                self.is_connected = False
                
                if attempt < self.retry_attempts - 1:
                    time.sleep(2)
                    continue
        
        return False


    def test_print(self) -> bool:
        """
        Print a test pattern. Routes to appropriate protocol handler.
        
        Returns:
            bool: True if test print successful
        """
        if self.protocol == 'startsp':
            return self._test_print_startsp()
        else:
            return self._test_print_escpos()


    def _test_print_escpos(self) -> bool:
        """
        Test print using ESC/POS protocol.
        
        Returns:
            bool: True if test print successful
        """
        if self.simulation_mode:
            logger.info("Simulation: Would print test pattern")
            return True
        
        if not self.is_connected:
            if not self.connect():
                return False
        
        try:
            self.printer.set(align='center', text_type='B')
            self.printer.text('Thermal Printer Test\n')
            self.printer.set(align='left', text_type='normal')
            self.printer.text('='*32 + '\n')
            self.printer.text('Status: OK\n')
            self.printer.text('Width: 80mm (640px @ 203 DPI)\n')
            self.printer.text('='*32 + '\n')
            self.printer.text('\n\n\n')
            self.printer.cut()
            
            logger.info("Test print successful")
            
            # Auto-disconnect Bluetooth to free up printer for other devices
            if self.connection_type == 'bluetooth':
                logger.info("Auto-disconnecting Bluetooth printer after successful test print")
                time.sleep(1)  # Wait a moment before disconnect
                self.disconnect()
            
            return True
            
        except Exception as e:
            logger.error(f"Error during test print: {e}")
            self.is_connected = False
            return False


    def _test_print_startsp(self) -> bool:
        """
        Test print using StarTSP protocol.
        
        Returns:
            bool: True if test print successful
        """
        if self.simulation_mode:
            logger.info("Simulation: Would print StarTSP test pattern")
            return True
        
        if not self.is_connected:
            if not self.connect():
                return False
        
        try:            
            # Verify connection is still alive
            if not self.startsp_serial or not self.startsp_serial.is_open:
                logger.error("StarTSP serial connection is not open")
                self.is_connected = False
                return False
            
            # Create a test image with PIL
            img = Image.new('RGB', (640, 400), color='white')
            draw = ImageDraw.Draw(img)
            
            # Try to load a larger font, fallback to default if not available
            try:
                font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
                font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            except:
                font_large = ImageFont.load_default()
                font_medium = ImageFont.load_default()
            
            # Draw test pattern with thicker lines
            draw.rectangle((10, 10, 630, 390), outline='black', width=8)
            draw.text((120, 50), "Star TSP Printer Test", fill='black', font=font_large)
            draw.text((220, 120), "Status: OK", fill='black', font=font_medium)
            draw.text((50, 170), "Width: 80mm (640px @ 203 DPI)", fill='black', font=font_medium)
            draw.text((140, 220), "Protocol: StarTSP", fill='black', font=font_medium)
            
            # Draw thicker pattern lines
            for i in range(20, 620, 30):
                draw.line([(i, 280), (i, 370)], fill='black', width=6)
            
            # Convert to raster
            logger.info("Converting image to StarTSP raster format...")
            raster = StarTSPImage.imageToRaster(img, cut=True)
            logger.info(f"Raster size: {len(raster)} bytes")
            
            # Send to printer
            logger.info("Sending raster data to printer...")
            bytes_written = self.startsp_serial.write(raster)
            logger.info(f"Wrote {bytes_written} bytes to printer")
            self.startsp_serial.flush()
            
            logger.info("StarTSP test print successful")
            
            # Auto-disconnect Bluetooth to free up printer for other devices
            if self.connection_type == 'bluetooth':
                logger.info("Auto-disconnecting Bluetooth printer after successful test print")
                time.sleep(1)  # Wait a moment before disconnect
                self.disconnect()
            
            return True
            
        except OSError as e:
            logger.error(f"I/O Error during StarTSP test print: {e}")
            logger.error("Possible causes:")
            logger.error("  - Bluetooth connection dropped")
            logger.error("  - Printer powered off or out of range")
            logger.error("  - /dev/rfcomm0 device disconnected")
            logger.error("  - Printer buffer overflow (image too large)")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"Error during StarTSP test print: {e}")
            self.is_connected = False
            return False
    
    def get_status(self) -> dict:
        """
        Get printer status.
        
        Returns:
            dict: Printer status information including connection type and protocol
        """
        status = {
            'connected': self.is_connected,
            'protocol': self.protocol,
            'simulation_mode': self.simulation_mode,
            'connection_type': self.connection_type,
        }
        
        if self.connection_type == 'bluetooth' and self.bluetooth_mac:
            status['bluetooth_mac'] = self.bluetooth_mac
        
        return status
    
    def __del__(self):
        """Cleanup on deletion."""
        self.disconnect()
