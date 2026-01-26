"""
Unified printer management interface.
Provides a single entry point for printer operations across different protocols and connection types.
"""

import json
import logging
import time
from typing import Optional, List, Dict

from .escpos_printer import ESCPOSPrinter
from .startsp_printer import StarTSPPrinter
from .bluetooth import BluetoothConnection
from .exceptions import PrinterError, InvalidConfigurationError

logger = logging.getLogger(__name__)


class PrinterManager:
    """
    Unified printer management interface.
    Maintains backward compatibility with PrinterHandler API.
    """
    
    def __init__(self, config_path: str = 'config.json'):
        """
        Initialize printer manager with configuration.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config = self._load_config(config_path)
        
        self.printer = None  # Current printer implementation (ESCPOSPrinter or StarTSPPrinter)
        self.protocol = self.config['printer'].get('protocol', 'escpos')
        self.simulation_mode = False
        self.retry_attempts = self.config['printer'].get('retry_attempts', 3)
        
        # Backward compatibility attributes
        self.is_connected = False
        self.connection_type = None
        self.bluetooth_mac = None
        
        logger.info("[Manager] " + "="*60)
        logger.info("[Manager] PrinterManager Initialization")
        logger.info(f"[Manager] Protocol: {self.protocol}")
        logger.info(f"[Manager] Connection type: {self.config['printer'].get('type', 'usb')}")
        logger.info(f"[Manager] Bluetooth MAC: {self.config['printer'].get('bluetooth_mac', 'Not configured')}")
        logger.info("[Manager] " + "="*60)
        
        # Attempt to connect to printer
        logger.info("[Manager] Attempting to connect to printer...")
        result = self.connect()
        logger.info(f"[Manager] Connection attempt result: {result}, is_connected = {self.is_connected}")
        
        if not result:
            self.simulation_mode = True
            logger.warning("[Manager] " + "*" * 60)
            logger.warning("[Manager] RUNNING IN SIMULATION MODE (NO PRINTER CONNECTED)")
            logger.warning("[Manager] " + "*" * 60)
        else:
            logger.info("[Manager] " + "*" * 60)
            logger.info(f"[Manager] PRINTER CONNECTED SUCCESSFULLY via {self.connection_type}")
            logger.info("[Manager] " + "*" * 60)
    
    def _load_config(self, config_path: str) -> dict:
        """
        Load configuration from file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Configuration dictionary
            
        Raises:
            InvalidConfigurationError: If config cannot be loaded
        """
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            logger.debug(f"[Manager] Configuration loaded from {config_path}")
            return config
        except Exception as e:
            logger.error(f"[Manager] Failed to load configuration: {e}")
            raise InvalidConfigurationError(
                f"Failed to load configuration from {config_path}",
                context={'path': config_path, 'error': str(e)}
            )
    
    def _save_config(self):
        """Save current configuration to file."""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("[Manager] Configuration saved successfully")
        except Exception as e:
            logger.error(f"[Manager] Failed to save configuration: {e}")
    
    def _create_printer_instance(self):
        """
        Create appropriate printer instance based on protocol.
        
        Returns:
            Printer instance (ESCPOSPrinter or StarTSPPrinter)
        """
        if self.protocol == 'startsp':
            return StarTSPPrinter(self.config['printer'])
        else:
            return ESCPOSPrinter(self.config['printer'])
    
    def connect(self) -> bool:
        """
        Connect to printer using configured connection type and protocol.
        
        Returns:
            True if connection successful
        """
        try:
            printer_config = self.config['printer']
            conn_type = printer_config.get('type', 'usb')
            
            # Create printer instance if needed
            if not self.printer:
                self.printer = self._create_printer_instance()
            
            # Attempt connection based on type
            if conn_type == 'bluetooth':
                success = self._connect_bluetooth()
            elif conn_type == 'usb':
                success = self._connect_usb()
            elif conn_type == 'auto':
                # Try Bluetooth first if configured, then USB
                logger.info("[Manager] Auto-detect mode: trying Bluetooth first...")
                success = self._connect_bluetooth()
                
                if not success:
                    logger.info("[Manager] Bluetooth failed, trying USB...")
                    success = self._connect_usb()
            else:
                logger.error(f"[Manager] Unknown connection type: {conn_type}")
                return False
            
            # Update status
            if success:
                self.is_connected = True
                self._update_connection_info()
                logger.info(f"[Manager] Successfully connected via {self.connection_type}")
            else:
                self.is_connected = False
                self.connection_type = None
            
            return success
            
        except Exception as e:
            logger.error(f"[Manager] Error in connect(): {e}")
            self.is_connected = False
            self.connection_type = None
            return False
    
    def _connect_usb(self) -> bool:
        """
        Connect via USB.
        
        Returns:
            True if connection successful
        """
        try:
            if self.protocol == 'startsp':
                logger.error("[Manager] USB not supported for StarTSP protocol")
                return False
            
            return self.printer.connect_usb()
        except Exception as e:
            logger.error(f"[Manager] USB connection failed: {e}")
            return False
    
    def _connect_bluetooth(self) -> bool:
        """
        Connect via Bluetooth.
        
        Returns:
            True if connection successful
        """
        try:
            mac = self.config['printer'].get('bluetooth_mac')
            if not mac:
                logger.debug("[Manager] No Bluetooth MAC configured, skipping Bluetooth")
                return False
            
            return self.printer.connect_bluetooth()
        except Exception as e:
            logger.error(f"[Manager] Bluetooth connection failed: {e}")
            return False
    
    def _update_connection_info(self):
        """Update connection information from printer instance."""
        if self.printer:
            status = self.printer.get_status()
            self.connection_type = status.get('connection_type')
            self.bluetooth_mac = status.get('mac_address')
    
    def disconnect(self):
        """Disconnect from printer."""
        if self.printer:
            self.printer.disconnect()
        
        self.is_connected = False
        self.connection_type = None
        self.bluetooth_mac = None
        logger.info("[Manager] Printer disconnected")
    
    def print_image(self, image_path: str) -> bool:
        """
        Print an image to the thermal printer with automatic retry.
        
        Args:
            image_path: Path to processed image file
            
        Returns:
            True if print successful
        """
        if self.simulation_mode:
            logger.info(f"[Manager] Simulation: Would print image {image_path}")
            return True
        
        if not self.printer:
            logger.error("[Manager] No printer instance available")
            return False
        
        try:
            success = self.printer.print_image(image_path, auto_reconnect=True)
            
            # Update connection status after print
            if success:
                self._update_connection_info()
                self.is_connected = self.printer.is_connected()
            
            return success
        except Exception as e:
            logger.error(f"[Manager] Print failed: {e}")
            self.is_connected = False
            return False
    
    def test_print(self) -> bool:
        """
        Print a test pattern.
        
        Returns:
            True if test print successful
        """
        if self.simulation_mode:
            logger.info("[Manager] Simulation: Would print test pattern")
            return True
        
        if not self.is_connected:
            logger.warning("[Manager] Printer not connected, attempting to connect...")
            if not self.connect():
                logger.error("[Manager] Failed to connect for test print")
                return False
        
        if not self.printer:
            logger.error("[Manager] No printer instance available")
            return False
        
        try:
            success = self.printer.test_print()
            
            # Update connection status after test print
            if success:
                self._update_connection_info()
                self.is_connected = self.printer.is_connected()
            
            return success
        except Exception as e:
            logger.error(f"[Manager] Test print failed: {e}")
            self.is_connected = False
            return False
    
    def switch_protocol(self, new_protocol: str) -> bool:
        """
        Switch printer protocol at runtime.
        
        Args:
            new_protocol: 'escpos' or 'startsp'
            
        Returns:
            True if switch successful
        """
        if new_protocol not in ['escpos', 'startsp']:
            logger.error(f"[Manager] Invalid protocol: {new_protocol}")
            return False
        
        # Disconnect current connection
        if self.is_connected:
            logger.info(f"[Manager] Disconnecting from {self.protocol} printer before protocol switch")
            time.sleep(1)
            self.disconnect()
        
        # Update protocol
        old_protocol = self.protocol
        self.protocol = new_protocol
        
        # Create new printer instance
        self.printer = self._create_printer_instance()
        
        # Update config file
        self.config['printer']['protocol'] = new_protocol
        self._save_config()
        
        logger.info(f"[Manager] Protocol switched from {old_protocol} to {new_protocol}")
        return True
    
    def scan_bluetooth_devices(self, timeout: int = 10) -> List[Dict]:
        """
        Scan for nearby Bluetooth devices.
        
        Args:
            timeout: Scan duration in seconds
            
        Returns:
            List of devices with format [{"name": str, "mac": str, "class": int, "is_printer": bool, "is_tsp100": bool}]
        """
        try:
            bt_conn = BluetoothConnection(self.config['printer'])
            devices = bt_conn.scan_devices(timeout)
            logger.info(f"[Manager] Bluetooth scan found {len(devices)} devices")
            return devices
        except Exception as e:
            logger.error(f"[Manager] Bluetooth scan failed: {e}")
            return []
    
    def pair_bluetooth_device(self, mac: str, timeout: int = 30) -> bool:
        """
        Pair a Bluetooth device at OS level.
        
        Args:
            mac: MAC address to pair
            timeout: Maximum time to wait for pairing (seconds)
            
        Returns:
            True if pairing successful
        """
        try:
            bt_conn = BluetoothConnection(self.config['printer'])
            bt_conn.pair_device(mac, timeout)
            logger.info(f"[Manager] Successfully paired with device {mac}")
            return True
        except Exception as e:
            logger.error(f"[Manager] Pairing failed: {e}")
            return False
    
    def unpair_bluetooth_device(self, mac: str) -> bool:
        """
        Unpair/remove a Bluetooth device at OS level.
        
        Args:
            mac: MAC address to unpair
            
        Returns:
            True if unpaired successfully
        """
        try:
            bt_conn = BluetoothConnection(self.config['printer'])
            bt_conn.unpair_device(mac)
            logger.info(f"[Manager] Successfully unpaired device {mac}")
            return True
        except Exception as e:
            logger.error(f"[Manager] Unpair failed: {e}")
            return False
    
    def check_bluetooth_pairing(self, mac: str) -> bool:
        """
        Check if a Bluetooth device is paired at OS level.
        
        Args:
            mac: MAC address to check
            
        Returns:
            True if device appears to be paired
        """
        try:
            bt_conn = BluetoothConnection(self.config['printer'])
            return bt_conn.check_pairing(mac)
        except Exception as e:
            logger.debug(f"[Manager] Could not check pairing status: {e}")
            return False
    
    def get_status(self) -> dict:
        """
        Get printer status.
        
        Returns:
            Dictionary with printer status information
        """
        status = {
            'connected': self.is_connected,
            'protocol': self.protocol,
            'simulation_mode': self.simulation_mode,
            'connection_type': self.connection_type,
        }
        
        if self.connection_type == 'bluetooth' and self.bluetooth_mac:
            status['bluetooth_mac'] = self.bluetooth_mac
        
        # Add printer-specific status if available
        if self.printer and self.is_connected:
            try:
                printer_status = self.printer.get_status()
                status.update(printer_status)
            except Exception as e:
                logger.debug(f"[Manager] Could not get printer status: {e}")
        
        return status
    
    # New encapsulation methods for better API design
    
    def get_config(self) -> dict:
        """
        Get current configuration.
        
        Returns:
            Configuration dictionary
        """
        return self.config.copy()
    
    def update_config(self, key: str, value, save: bool = True):
        """
        Update a configuration value.
        
        Args:
            key: Configuration key (dot-notation supported, e.g., 'printer.protocol')
            value: New value
            save: Whether to save config to file
        """
        # Support dot notation for nested keys
        keys = key.split('.')
        config = self.config
        
        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        # Set the value
        old_value = config.get(keys[-1])
        config[keys[-1]] = value
        logger.info(f"[Manager] Updated config: {key} = {value} (was: {old_value})")
        
        # Save if requested
        if save:
            self._save_config()
    
    def set_protocol(self, protocol: str) -> bool:
        """
        Set the printer protocol.
        Wrapper around switch_protocol for better naming.
        
        Args:
            protocol: 'escpos' or 'startsp'
            
        Returns:
            True if successful
        """
        return self.switch_protocol(protocol)
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.disconnect()
        except Exception as e:
            logger.debug(f"[Manager] Error during cleanup: {e}")
