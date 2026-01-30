"""
Bluetooth connection management for thermal printers.
Handles Bluetooth scanning, pairing, RFCOMM binding, and connection.
"""

import logging
import os
import re
import select
import subprocess
import time
import serial # type: ignore
from typing import List, Dict, Optional

from .exceptions import BluetoothPairingError, PrinterConnectionError

try:
    from escpos.printer import Serial as EscposSerial # type: ignore
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False

logger = logging.getLogger(__name__)


class BluetoothConnection:
    """Manages Bluetooth printer connections."""
    
    def __init__(self, config: dict):
        """
        Initialize Bluetooth connection handler.
        
        Args:
            config: Printer configuration dictionary
        """
        self.config = config
        self.serial_connection = None
        self.mac_address = None
        self.rfcomm_device = '/dev/rfcomm0'
        self.rfcomm_port = config.get('bluetooth_port', 1)
    
    def scan_devices(self, timeout: int = 10) -> List[Dict]:
        """
        Scan for nearby Bluetooth devices using hcitool.
        
        Args:
            timeout: Scan duration in seconds
            
        Returns:
            List of devices with format [{"name": str, "mac": str, "class": int, "is_printer": bool, "is_tsp100": bool}]
        """
        try:
            logger.info(f"[Bluetooth] Scanning for devices ({timeout}s)...")
            
            # Use hcitool scan for simple, cache-free scanning
            result = subprocess.run(
                ['hcitool', 'scan', '--flush'],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            devices = []
            if result.returncode == 0:
                # Parse output: "\tXX:XX:XX:XX:XX:XX\tDevice Name"
                for line in result.stdout.strip().split('\n'):
                    # Skip header line and empty lines
                    if not line.strip() or 'Scanning' in line:
                        continue
                    
                    # Split on tabs and filter empty parts
                    parts = [p.strip() for p in line.split('\t') if p.strip()]
                    
                    if len(parts) >= 1 and ':' in parts[0]:
                        mac = parts[0]
                        name = parts[1] if len(parts) > 1 else 'Unknown Device'
                        
                        # Get additional device info
                        device_info = self._get_device_info(mac, name)
                        devices.append(device_info)
            
            logger.info(f"[Bluetooth] Found {len(devices)} devices")
            return devices
            
        except FileNotFoundError:
            logger.error("[Bluetooth] hcitool not found. Install with: sudo apt-get install bluez")
            return []
        except Exception as e:
            logger.error(f"[Bluetooth] Scan failed: {e}")
            return []
    
    def _get_device_info(self, mac: str, default_name: str) -> Dict:
        """
        Get detailed information about a Bluetooth device.
        
        Args:
            mac: MAC address of device
            default_name: Default name if none found
            
        Returns:
            Dictionary with device information including RSSI and paired status
        """
        name = default_name
        is_printer = False
        is_paired = False
        dev_class = 0
        rssi = None
        
        try:
            info_result = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if info_result.returncode == 0:
                # Parse info output for Name, Class, RSSI, and Paired status
                for info_line in info_result.stdout.split('\n'):
                    # Get proper device name if available
                    if 'Name:' in info_line and (name == 'Unknown Device' or not name):
                        try:
                            name = info_line.split('Name:')[1].strip()
                        except IndexError:
                            pass
                    
                    # Get device class
                    if 'Class:' in info_line:
                        try:
                            class_str = info_line.split('Class:')[1].strip()
                            dev_class = int(class_str, 16)
                            # Device class 0x1680 = Imaging/Printer
                            is_printer = (dev_class & 0x1FFF) == 0x1680
                        except (ValueError, IndexError):
                            pass
                    
                    # Get RSSI (signal strength) - indicates device is in range
                    if 'RSSI:' in info_line:
                        try:
                            rssi_str = info_line.split('RSSI:')[1].strip()
                            rssi = int(rssi_str)
                        except (ValueError, IndexError):
                            pass
                    
                    # Check if paired
                    if 'Paired:' in info_line:
                        try:
                            paired_str = info_line.split('Paired:')[1].strip().lower()
                            is_paired = paired_str == 'yes'
                        except IndexError:
                            pass
                            
        except Exception as e:
            logger.debug(f"[Bluetooth] Could not get info for {mac}: {e}")
        
        # Also check name for printer indicators
        printer_keywords = ['PRINTER', 'TSP', 'STAR', 'EPSON', 'CITIZEN']
        if not is_printer and any(kw in name.upper() for kw in printer_keywords):
            is_printer = True
        
        return {
            'mac': mac,
            'name': name,
            'class': dev_class,
            'is_printer': is_printer,
            'is_tsp100': 'TSP100' in name.upper(),
            'is_paired': is_paired,
            'rssi': rssi
        }
    
    def check_pairing(self, mac: str) -> bool:
        """
        Check if a Bluetooth device is paired at OS level.
        
        Args:
            mac: MAC address to check
            
        Returns:
            True if device appears to be paired
        """
        try:
            result = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # If device info is returned, it's likely paired
            if result.returncode == 0 and 'Device' in result.stdout:
                logger.debug(f"[Bluetooth] Device {mac} appears to be paired")
                return True
            else:
                logger.debug(f"[Bluetooth] Device {mac} not currently paired")
                return False
                
        except Exception as e:
            logger.debug(f"[Bluetooth] Could not check pairing status: {e}")
            return False
    
    def unpair_device(self, mac: str) -> bool:
        """
        Unpair/remove a Bluetooth device at OS level.
        
        Args:
            mac: MAC address to unpair
            
        Returns:
            True if unpaired successfully or device wasn't paired
            
        Raises:
            BluetoothPairingError: If unpair operation fails unexpectedly
        """
        if not self._validate_mac_address(mac):
            raise BluetoothPairingError(
                f"Invalid MAC address format: {mac}",
                context={'mac': mac}
            )
        
        logger.info(f"[Bluetooth] Attempting to unpair device {mac}...")
        
        try:
            # Check if device exists/is paired
            check_result = subprocess.run(
                ['bluetoothctl', 'info', mac],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # If device doesn't exist, nothing to unpair
            if check_result.returncode != 0 or 'not available' in check_result.stderr.lower():
                logger.info(f"[Bluetooth] Device {mac} not found or not paired")
                return True
            
            # Device exists, remove it
            result = subprocess.run(
                ['bluetoothctl', 'remove', mac],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 or 'removed' in result.stdout.lower():
                logger.info(f"[Bluetooth] Successfully unpaired device {mac}")
                return True
            else:
                logger.warning(f"[Bluetooth] Unpair may have failed for {mac}")
                logger.debug(f"[Bluetooth] stdout: {result.stdout}")
                logger.debug(f"[Bluetooth] stderr: {result.stderr}")
                # Return True anyway since we tried and it might have worked
                return True
                
        except subprocess.TimeoutExpired:
            logger.error(f"[Bluetooth] Unpair operation timed out for {mac}")
            raise BluetoothPairingError(
                f"Unpair operation timed out for {mac}",
                context={'mac': mac}
            )
        except Exception as e:
            logger.error(f"[Bluetooth] Error unpairing device {mac}: {e}")
            raise BluetoothPairingError(
                f"Error unpairing device: {e}",
                context={'mac': mac, 'error': str(e)}
            )
    
    # NOTE: this function is actually so retarded...
    def pair_device(self, mac: str, timeout: int = 30) -> bool:
        """
        Pair a Bluetooth device at OS level using bluetoothctl.
        
        Args:
            mac: MAC address to pair
            timeout: Maximum time to wait for pairing (seconds)
            
        Returns:
            True if pairing successful
            
        Raises:
            BluetoothPairingError: If pairing fails
        """
        if not self._validate_mac_address(mac):
            raise BluetoothPairingError(
                f"Invalid MAC address format: {mac}",
                context={'mac': mac}
            )
        
        logger.info(f"[Bluetooth] Attempting to pair with device {mac}...")
        
        # First, check if already paired
        if self.check_pairing(mac):
            logger.info(f"[Bluetooth] Device {mac} is already paired")
            return True
        
        process = None
        try:
            # Start persistent bluetoothctl session
            logger.info(f"[Bluetooth] Starting bluetoothctl session...")
            process = subprocess.Popen(
                ['bluetoothctl'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            # Wait for bluetoothctl to connect and show prompt
            logger.debug(f"[Bluetooth] Waiting for bluetoothctl to connect to bluetoothd...")
            ready = False
            start_wait = time.time()
            while time.time() - start_wait < 5:  # 5 second timeout
                try:
                    import select
                    if select.select([process.stdout], [], [], 0.1)[0]:
                        line = process.stdout.readline()
                        logger.debug(f"[Bluetooth] Init: {line.strip()}")
                        # Look for the prompt "[bluetoothctl]>" or "Agent registered"
                        if '[bluetoothctl]' in line or 'Agent registered' in line:
                            ready = True
                            break
                except:
                    pass
            
            if not ready:
                raise BluetoothPairingError(
                    "Bluetoothctl failed to initialize - bluetoothd may not be running",
                    context={'mac': mac}
                )
            
            logger.debug("[Bluetooth] Bluetoothctl ready")
            
            # Ensure Bluetooth is powered on and agent is ready
            logger.debug("[Bluetooth] Sending power on command...")
            process.stdin.write('power on\n')
            process.stdin.flush()
            time.sleep(0.5)
            
            logger.debug("[Bluetooth] Enabling agent...")
            process.stdin.write('agent on\n')
            process.stdin.flush()
            time.sleep(0.5)
            
            logger.debug("[Bluetooth] Setting default agent...")
            process.stdin.write('default-agent\n')
            process.stdin.flush()
            time.sleep(0.5)
            
            # Start scanning
            logger.info(f"[Bluetooth] Starting scan...")
            process.stdin.write('scan on\n')
            process.stdin.flush()
            
            # Wait for scan to discover devices
            logger.info(f"[Bluetooth] Scanning for {mac} (15 seconds)...")
            time.sleep(15)
            
            # Consume/clear output from scan command
            logger.debug("[Bluetooth] Consuming scan output buffer...")
            try:
                import select
                while True:
                    if select.select([process.stdout], [], [], 0.1)[0]:
                        line = process.stdout.readline()
                        if not line:
                            break
                    else:
                        break  # No more data available
            except:
                pass
            
            # Stop scanning before reading devices
            logger.debug(f"[Bluetooth] Stopping scan...")
            process.stdin.write('scan off\n')
            process.stdin.flush()
            time.sleep(0.5)
            
            # Consume output from scan off command
            try:
                while True:
                    if select.select([process.stdout], [], [], 0.1)[0]:
                        line = process.stdout.readline()
                        if not line:
                            break
                    else:
                        break
            except:
                pass
            
            # Now request device list - buffer should be clear
            logger.debug(f"[Bluetooth] Requesting device list...")
            process.stdin.write('devices\n')
            process.stdin.flush()
            time.sleep(0.5)
            
            # Read ONLY the devices output
            output_lines = []
            try:
                read_timeout = 3
                start_read = time.time()
                while time.time() - start_read < read_timeout:
                    if select.select([process.stdout], [], [], 0.1)[0]:
                        line = process.stdout.readline()
                        if line:
                            line_stripped = line.strip()
                            logger.debug(f"[Bluetooth] Device: {line_stripped}")
                            output_lines.append(line)
                            # Stop if we see the next prompt (means command finished)
                            if '[bluetoothctl]' in line_stripped and line_stripped.endswith('>'):
                                logger.debug("[Bluetooth] Reached end of device list (prompt detected)")
                                break
                        else:
                            break
                    else:
                        # No more data, exit if we got some output
                        if output_lines:
                            break
            except Exception as e:
                logger.warning(f"[Bluetooth] Error reading devices: {e}")
            
            devices_output = ''.join(output_lines)
            logger.debug(f"[Bluetooth] Devices output: {len(output_lines)} lines, {len(devices_output)} chars")
            if mac.upper() not in devices_output.upper():
                error_msg = f"Device {mac} not found after scan"
                logger.error(f"[Bluetooth] {error_msg}")
                logger.debug(f"[Bluetooth] Available devices: {devices_output.strip()}")
                raise BluetoothPairingError(
                    error_msg,
                    context={'mac': mac, 'available_devices': devices_output}
                )
            
            logger.info(f"[Bluetooth] Device {mac} found! Proceeding to pair...")
            
            # Attempt pairing
            logger.info(f"[Bluetooth] Sending pair command...")
            process.stdin.write(f'pair {mac}\n')
            process.stdin.flush()
            
            # Wait for pairing to complete
            start_time = time.time()
            pairing_success = False
            
            while time.time() - start_time < timeout:
                try:
                    line = process.stdout.readline()
                    if line:
                        logger.debug(f"[Bluetooth] {line.strip()}")
                        if 'Pairing successful' in line or 'paired successfully' in line.lower():
                            pairing_success = True
                            break
                        elif 'Failed to pair' in line or 'pairing failed' in line.lower():
                            break
                except:
                    pass
                time.sleep(0.1)
            
            if pairing_success:
                logger.info(f"[Bluetooth] Successfully paired with device {mac}")
                
                # Trust the device for auto-reconnection
                try:
                    process.stdin.write(f'trust {mac}\n')
                    process.stdin.flush()
                    time.sleep(1)
                    logger.debug(f"[Bluetooth] Device {mac} marked as trusted")
                except Exception as e:
                    logger.debug(f"[Bluetooth] Could not mark device as trusted: {e}")
                
                return True
            else:
                error_msg = f"Failed to pair with device {mac}"
                logger.error(f"[Bluetooth] {error_msg}")
                logger.error("[Bluetooth] Make sure:")
                logger.error("[Bluetooth]   1. Bluetooth is enabled: bluetoothctl power on")
                logger.error("[Bluetooth]   2. Device is in pairing mode")
                logger.error("[Bluetooth]   3. Device is within range")
                
                raise BluetoothPairingError(
                    error_msg,
                    context={'mac': mac}
                )
                
        except subprocess.TimeoutExpired:
            error_msg = f"Pairing timed out after {timeout} seconds"
            logger.error(f"[Bluetooth] {error_msg}")
            logger.error("[Bluetooth] Device may not be in pairing mode or out of range")
            
            raise BluetoothPairingError(
                error_msg,
                context={'mac': mac, 'timeout': timeout}
            )
        except Exception as e:
            if isinstance(e, BluetoothPairingError):
                raise
            logger.error(f"[Bluetooth] Error during pairing: {e}")
            
            raise BluetoothPairingError(
                f"Error during Bluetooth pairing: {e}",
                context={'mac': mac, 'error': str(e)}
            )
        finally:
            # Clean up: quit bluetoothctl session
            if process:
                try:
                    process.stdin.write('quit\n')
                    process.stdin.flush()
                    process.wait(timeout=3)
                except:
                    process.kill()
                logger.debug("[Bluetooth] Bluetoothctl session closed")
    
    def bind_rfcomm(self, mac: str, port: Optional[int] = None) -> str:
        """
        Bind Bluetooth MAC address to RFCOMM device.
        
        Args:
            mac: MAC address to bind
            port: RFCOMM port (if None, uses config value)
            
        Returns:
            Path to RFCOMM device (e.g., '/dev/rfcomm0')
            
        Raises:
            PrinterConnectionError: If binding fails
        """
        if port is None:
            port = self.rfcomm_port
        
        logger.info(f"[Bluetooth] Binding {mac} to {self.rfcomm_device} on port {port}...")
        
        # Try to release any existing binding first
        try:
            subprocess.run(
                ['sudo', 'rfcomm', 'release', '0'],
                capture_output=True,
                timeout=5,
                check=False
            )
            logger.debug("[Bluetooth] Released existing RFCOMM binding")
        except Exception as e:
            logger.debug(f"[Bluetooth] Could not release existing binding: {e}")
        
        # Bind the Bluetooth MAC to rfcomm0
        try:
            result = subprocess.run(
                ['sudo', 'rfcomm', 'bind', '0', mac, str(port)],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                error_msg = f"Failed to bind rfcomm device: {result.stderr}"
                logger.error(f"[Bluetooth] {error_msg}")
                logger.error(f"[Bluetooth] Try manually: sudo rfcomm bind 0 {mac} {port}")
                
                raise PrinterConnectionError(
                    error_msg,
                    context={'mac': mac, 'port': port, 'stderr': result.stderr}
                )
            
            # Wait a moment for the device to be ready
            time.sleep(1)
            
            # Check if device exists
            if not os.path.exists(self.rfcomm_device):
                error_msg = f"RFCOMM device {self.rfcomm_device} not created"
                logger.error(f"[Bluetooth] {error_msg}")
                
                raise PrinterConnectionError(
                    error_msg,
                    context={'device': self.rfcomm_device, 'mac': mac}
                )
            
            logger.info(f"[Bluetooth] Successfully bound to {self.rfcomm_device}")
            return self.rfcomm_device
            
        except subprocess.TimeoutExpired:
            raise PrinterConnectionError(
                "RFCOMM bind operation timed out",
                context={'mac': mac, 'port': port}
            )
        except Exception as e:
            if isinstance(e, PrinterConnectionError):
                raise
            raise PrinterConnectionError(
                f"Failed to bind RFCOMM: {e}",
                context={'mac': mac, 'port': port, 'error': str(e)}
            )
    
    def connect(self, mac_address: Optional[str] = None, port: Optional[int] = None, 
                protocol: str = 'escpos') -> bool:
        """
        Connect to Bluetooth thermal printer.
        
        Args:
            mac_address: Bluetooth MAC address (if None, uses config)
            port: RFCOMM port (if None, uses config)
            protocol: 'escpos' or 'startsp'
            
        Returns:
            True if connection successful
            
        Raises:
            PrinterConnectionError: If connection fails
            BluetoothPairingError: If pairing fails
        """
        # Get MAC from parameter or config
        mac = mac_address or self.config.get('bluetooth_mac')
        if not mac:
            raise PrinterConnectionError(
                "No Bluetooth MAC address configured. Set 'bluetooth_mac' in config.json"
            )
        
        # Get port from parameter or config
        if port is None:
            port = self.rfcomm_port
        
        # Validate MAC address format
        if not self._validate_mac_address(mac):
            raise PrinterConnectionError(
                f"Invalid MAC address format: {mac}",
                context={'mac': mac}
            )
        
        # Check if device is paired, attempt pairing if not
        if not self.check_pairing(mac):
            logger.info(f"[Bluetooth] Device {mac} not paired. Running scan and attempting pairing...")
            
            # Run a quick scan to make the device available for pairing
            logger.debug("[Bluetooth] Running quick scan...")
            try:
                scan_cmd = 'echo -e "scan on\\nquit" | timeout 5 bluetoothctl'
                subprocess.run(
                    scan_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=7
                )
                time.sleep(1)
                logger.debug("[Bluetooth] Scan completed")
            except Exception as e:
                logger.warning(f"[Bluetooth] Scan failed but continuing: {e}")
            
            # Attempt pairing
            self.pair_device(mac)
        
        # Bind RFCOMM device
        rfcomm_device = self.bind_rfcomm(mac, port)
        
        # Create serial connection based on protocol
        # NOTE: should probably moved this to the proper printer class later, or have it passed in...
        try:
            logger.info(f"[Bluetooth] Creating {protocol} serial connection over Bluetooth...")
            
            if protocol == 'startsp':
                # For StarTSP, use pyserial directly
                self.serial_connection = serial.Serial(
                    rfcomm_device,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=10
                )
                logger.info(f"[Bluetooth] StarTSP serial connection created")
            else:
                # For ESC/POS, use python-escpos Serial printer
                if not ESCPOS_AVAILABLE:
                    raise PrinterConnectionError(
                        "ESC/POS not available but protocol is set to escpos"
                    )
                
                self.serial_connection = EscposSerial(
                    devfile=rfcomm_device,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=10
                )
                logger.info(f"[Bluetooth] ESC/POS serial connection created")
            
            self.mac_address = mac
            logger.info(f"[Bluetooth] Successfully connected to {mac}")
            return True
            
        except Exception as e:
            logger.error(f"[Bluetooth] Failed to create connection: {type(e).__name__}: {e}")
            logger.error(f"[Bluetooth] Make sure device {mac} is paired at OS level")
            logger.error("[Bluetooth] And that rfcomm tools are installed: sudo apt-get install bluez")
            
            raise PrinterConnectionError(
                f"Failed to create Bluetooth connection: {e}",
                context={'mac': mac, 'port': port, 'protocol': protocol, 'error': str(e)}
            )
    
    def disconnect(self):
        """Disconnect from Bluetooth printer."""
        if self.serial_connection:
            try:
                # Close serial connection
                if hasattr(self.serial_connection, 'device') and hasattr(self.serial_connection.device, 'close'):
                    self.serial_connection.device.close()
                    logger.debug("[Bluetooth] Serial device closed")
                
                self.serial_connection.close()
                logger.info("[Bluetooth] Serial connection closed")
            except Exception as e:
                logger.debug(f"[Bluetooth] Error closing serial connection: {e}")
            finally:
                self.serial_connection = None
        
        # Release RFCOMM device
        try:
            logger.debug("[Bluetooth] Releasing RFCOMM device...")
            subprocess.run(
                ['sudo', 'rfcomm', 'release', '0'],
                capture_output=True,
                timeout=5,
                check=False
            )
            logger.debug("[Bluetooth] RFCOMM device released")
        except Exception as e:
            logger.debug(f"[Bluetooth] Could not release RFCOMM: {e}")
        
        self.mac_address = None
        logger.info("[Bluetooth] Disconnected")
    
    def is_connected(self) -> bool:
        """
        Check if Bluetooth printer is connected.
        
        Returns:
            True if connected
        """
        if not self.serial_connection:
            return False
        
        # For pyserial objects, check if port is open
        if hasattr(self.serial_connection, 'is_open'):
            return self.serial_connection.is_open
        
        # For escpos Serial objects, check if device exists
        if hasattr(self.serial_connection, 'device'):
            return self.serial_connection.device is not None
        
        return True
    
    def get_connection(self):
        """
        Get the underlying serial connection object.
        
        Returns:
            The serial connection object (pyserial or escpos Serial)
        """
        return self.serial_connection
    
    def _validate_mac_address(self, mac: str) -> bool:
        """
        Validate Bluetooth MAC address format.
        
        Args:
            mac: MAC address to validate
            
        Returns:
            True if valid format
        """
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        return bool(re.match(pattern, mac))
