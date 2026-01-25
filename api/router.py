import os
import json
import logging
from flask import Flask, request, jsonify, send_file, send_from_directory #type: ignore
from flask_cors import CORS #type: ignore
from image_handler import ImageHandler
from printer_handler import PrinterHandler
from gpio_handler import GPIOHandler

logger = logging.getLogger(__name__)

class Router:

    def __init__(self, image_handler: ImageHandler, printer_handler: PrinterHandler, gpio_handler: GPIOHandler,
                 config_path: str, image_db: dict, images_db_path: str):
        """Initialize Flask app and routes."""
        self.image_handler = image_handler
        self.printer_handler = printer_handler
        self.gpio_handler = gpio_handler
        self.config_path = config_path
        
        # Ensure images_db is always a dict
        if isinstance(image_db, dict):
            self.images_db = image_db
        elif isinstance(image_db, list):
            logger.warning("image_db passed as list, converting to dict")
            # If it's a list, try to convert it to a dict using 'id' as key
            self.images_db = {}
            for item in image_db:
                if isinstance(item, dict) and 'id' in item:
                    self.images_db[item['id']] = item
        else:
            logger.error(f"image_db has unexpected type: {type(image_db)}, initializing as empty dict")
            self.images_db = {}
        
        self.images_db_path = images_db_path

        with open(self.config_path, 'r') as f:
            self.config = json.load(f)
        
        # Create Flask app instance
        self.app = Flask(__name__, static_folder='static')
        CORS(self.app)
        
        # Register routes with decorators
        self._register_routes()
    
    def _register_routes(self):
        """Register all Flask routes with decorators."""
        # Main routes
        self.app.route('/')(self.index)
        self.app.route('/app.js')(self.serve_app_js)
        self.app.route('/style.css')(self.serve_style_css)
        self.app.route('/api/upload', methods=['POST'])(self.upload_image)
        self.app.route('/api/images', methods=['GET'])(self.list_images)
        self.app.route('/api/images/<image_id>', methods=['GET'])(self.get_image)
        self.app.route('/api/images/<image_id>', methods=['DELETE'])(self.delete_image)
        self.app.route('/api/images/<image_id>/process', methods=['POST'])(self.process_image)
        self.app.route('/api/images/<image_id>/preview', methods=['GET'])(self.get_preview)
        self.app.route('/api/images/<image_id>/print', methods=['POST'])(self.print_image)
        self.app.route('/api/config', methods=['GET'])(self.get_config)
        self.app.route('/api/config', methods=['POST'])(self.update_config)
        self.app.route('/api/printer/status', methods=['GET'])(self.get_printer_status)
        self.app.route('/api/printer/reconnect', methods=['POST'])(self.reconnect_printer)
        self.app.route('/api/printer/test', methods=['POST'])(self.test_printer)
        self.app.route('/api/printer/protocol', methods=['GET'])(self.get_printer_protocol)
        self.app.route('/api/printer/protocol', methods=['POST'])(self.switch_printer_protocol)
        self.app.route('/api/printer/bluetooth/scan', methods=['GET'])(self.scan_bluetooth)
        self.app.route('/api/printer/bluetooth/connect', methods=['POST'])(self.connect_bluetooth)
        self.app.route('/api/printer/bluetooth/disconnect', methods=['POST'])(self.disconnect_bluetooth)
        self.app.route('/api/printer/bluetooth/unpair', methods=['POST'])(self.unpair_bluetooth)
        self.app.route('/api/printer/switch', methods=['POST'])(self.switch_connection)
        self.app.route('/api/gpio/status', methods=['GET'])(self.get_gpio_status)
        self.app.route('/api/gpio/simulate/<int:button_number>', methods=['POST'])(self.simulate_button)

    # NOTE: this way of storing image data is really not ideal for scalability
    def save_images_db(self, db):
        """Save images database to file."""
        with open(self.images_db_path, 'w') as f:
            json.dump(db, f, indent=2)

    def save_config(self):
        """Save current configuration to file."""
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)

    def _allowed_file(self, filename):
        """Check if file extension is allowed."""
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in self.config['global_settings']['allowed_extensions']


    def index(self):
        """Serve main web interface."""
        return send_from_directory('static', 'index.html')

    def serve_app_js(self):
        """Serve app.js from static folder."""
        return send_from_directory('static', 'app.js')

    def serve_style_css(self):
        """Serve style.css from static folder."""
        return send_from_directory('static', 'style.css')

    
    def upload_image(self):
        """
        Upload a new image.
        
        Returns:
            JSON with image metadata
        """
        try:
            if 'file' not in request.files:
                return jsonify({'error': 'No file provided'}), 400
            
            file = request.files['file']
            
            if file.filename == '':
                return jsonify({'error': 'No file selected'}), 400
            
            if not self._allowed_file(file.filename):
                return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, GIF, BMP'}), 400
            
            # Save uploaded image
            metadata = self.image_handler.save_uploaded_image(file)
            
            # Ensure images_db is a dict (defensive check)
            if not isinstance(self.images_db, dict):
                logger.error(f"images_db is {type(self.images_db)}, converting to dict")
                if isinstance(self.images_db, list):
                    # Convert list to dict
                    new_db = {}
                    for item in self.images_db:
                        if isinstance(item, dict) and 'id' in item:
                            new_db[item['id']] = item
                    self.images_db = new_db
                else:
                    self.images_db = {}
            
            # Store in database
            self.images_db[metadata['id']] = metadata
            self.save_images_db(self.images_db)
            
            # Process image with default settings (auto-fit)
            try:
                _, width, height = self.image_handler.process_image(
                    metadata['id'],
                    auto_fit=True
                )
                metadata['processed'] = True
                metadata['processed_width'] = width
                metadata['processed_height'] = height
                self.save_images_db(self.images_db)
            except Exception as e:
                logger.error(f"Error processing image: {e}")
            
            logger.info(f"Image uploaded: {metadata['id']}")
            return jsonify(metadata), 201
            
        except Exception as e:
            logger.error(f"Error uploading image: {e}")
            return jsonify({'error': str(e)}), 500


    def list_images(self):
        """
        Get list of all uploaded images.
        
        Returns:
            JSON array of image metadata
        """
        # Handle case where images_db might be a list instead of dict
        if isinstance(self.images_db, dict):
            return jsonify(list(self.images_db.values())), 200
        elif isinstance(self.images_db, list):
            logger.warning("images_db is a list instead of dict, converting...")
            # Convert list to dict if it's a list (shouldn't happen but defensive)
            return jsonify(self.images_db), 200
        else:
            logger.error(f"images_db has unexpected type: {type(self.images_db)}")
            return jsonify([]), 200


    def get_image(self, image_id):
        """
        Get metadata for specific image.
        
        Args:
            image_id: Image identifier
            
        Returns:
            JSON with image metadata
        """
        if image_id not in self.images_db:
            return jsonify({'error': 'Image not found'}), 404
        
        return jsonify(self.images_db[image_id]), 200


    def delete_image(self, image_id):
        """
        Delete an image.
        
        Args:
            image_id: Image identifier
            
        Returns:
            JSON with success status
        """
        try:
            if image_id not in self.images_db:
                return jsonify({'error': 'Image not found'}), 404
            
            for button, assigned_id in self.config['button_assignments'].items():
                if assigned_id == image_id:
                    self.config['button_assignments'][button] = None
            
            self.save_config()
            
            # Delete files
            self.image_handler.delete_image(image_id)
            
            # Remove from database
            del self.images_db[image_id]
            self.save_images_db(self.images_db)
            
            logger.info(f"Image deleted: {image_id}")
            return jsonify({'success': True}), 200
            
        except Exception as e:
            logger.error(f"Error deleting image: {e}")
            return jsonify({'error': str(e)}), 500


    def process_image(self, image_id):
        """
        Process image with specific settings.
        
        Args:
            image_id: Image identifier
            
        Body:
            - x_offset: Horizontal offset in pixels
            - y_offset: Vertical offset in pixels
            - auto_fit: Boolean for auto-fit mode
            
        Returns:
            JSON with processing result
        """
        try:
            if image_id not in self.images_db:
                return jsonify({'error': 'Image not found'}), 404
            
            data = request.get_json() or {}
            x_offset = data.get('x_offset', 0)
            y_offset = data.get('y_offset', 0)
            auto_fit = data.get('auto_fit', True)
            dither_method = data.get('dither_method', 'floyd_steinberg')
            raw_mode = data.get('raw_mode', False)
            
            _, width, height = self.image_handler.process_image(
                image_id,
                user_x_offset=x_offset,
                user_y_offset=y_offset,
                auto_fit=auto_fit,
                dither_method=dither_method,
                raw_mode=raw_mode
            )
            
            # Update metadata
            self.images_db[image_id]['processed'] = True
            self.images_db[image_id]['processed_width'] = width
            self.images_db[image_id]['processed_height'] = height
            self.images_db[image_id]['position'] = {'x': x_offset, 'y': y_offset}
            self.images_db[image_id]['auto_fit'] = auto_fit
            self.images_db[image_id]['dither_method'] = dither_method
            self.images_db[image_id]['raw_mode'] = raw_mode 
            self.save_images_db(self.images_db)
            
            mode_label = "RAW COLOR" if raw_mode else dither_method
            logger.info(f"Image processed: {image_id} with {mode_label}")
            return jsonify(self.images_db[image_id]), 200
            
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            return jsonify({'error': str(e)}), 500



    def get_preview(self, image_id):
        """
        Get processed preview image.
        
        Args:
            image_id: Image identifier
            
        Returns:
            PNG image file
        """
        try:
            preview_path = self.image_handler.get_processed_image(image_id)
            
            if not preview_path or not os.path.exists(preview_path):
                return jsonify({'error': 'Preview not found'}), 404
            
            return send_file(preview_path, mimetype='image/png')
            
        except Exception as e:
            logger.error(f"Error getting preview: {e}")
            return jsonify({'error': str(e)}), 500


    def print_image(self, image_id):
        """
        Test print an image.
        
        Args:
            image_id: Image identifier
            
        Returns:
            JSON with print status
        """
        try:
            if image_id not in self.images_db:
                return jsonify({'error': 'Image not found'}), 404
            
            processed_path = self.image_handler.get_processed_image(image_id)
            
            if not processed_path or not os.path.exists(processed_path):
                return jsonify({'error': 'Processed image not found'}), 404
            
            success = self.printer_handler.print_image(processed_path)
            
            if success:
                logger.info(f"Test print successful: {image_id}")
                return jsonify({'success': True, 'message': 'Print sent to printer'}), 200
            else:
                return jsonify({'error': 'Print failed'}), 500
                
        except Exception as e:
            logger.error(f"Error printing image: {e}")
            return jsonify({'error': str(e)}), 500


    def get_config(self):
        """
        Get current configuration including button assignments and printer settings.
        
        Returns:
            JSON with configuration
        """
        return jsonify({
            'button_assignments': self.config['button_assignments'],
            'image_settings': self.config['image_settings'],
            'printer': self.config['printer']
        }), 200


    def update_config(self):
        """
        Update configuration (button assignments).
        
        Body:
            - button_assignments: Dict mapping button numbers to image IDs
            
        Returns:
            JSON with updated configuration
        """
        try:
            data = request.get_json()
            
            if 'button_assignments' not in data:
                return jsonify({'error': 'Missing button_assignments'}), 400
            
            # Update button assignments
            self.config['button_assignments'] = data['button_assignments']
            
            self.save_config()
            
            logger.info(f"Configuration updated: {data['button_assignments']}")
            return jsonify(self.config['button_assignments']), 200
            
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return jsonify({'error': str(e)}), 500


    def get_printer_status(self):
        """
        Get printer connection status.
        
        Returns:
            JSON with printer status
        """
        return jsonify(self.printer_handler.get_status()), 200


    def reconnect_printer(self):
        """
        Attempt to reconnect to the printer using current configuration.
        
        Returns:
            JSON with reconnection result and status
        """
        try:
            logger.info("API: Reconnect request received")
            
            # Disconnect current connection if any
            if self.printer_handler.is_connected:
                logger.info("Disconnecting existing connection...")
                self.printer_handler.disconnect()
            
            # Attempt reconnection
            logger.info("Attempting to reconnect...")
            success = self.printer_handler.connect()
            
            # Update simulation mode based on connection result
            if success:
                self.printer_handler.simulation_mode = False
                logger.info("Reconnection successful")
                return jsonify({
                    'success': True,
                    'message': 'Printer reconnected successfully',
                    'status': self.printer_handler.get_status()
                }), 200
            else:
                self.printer_handler.simulation_mode = True
                logger.warning("Reconnection failed, remaining in simulation mode")
                return jsonify({
                    'success': False,
                    'message': 'Failed to connect to printer. Check logs for details.',
                    'status': self.printer_handler.get_status()
                }), 200
                
        except Exception as e:
            logger.error(f"Error during reconnection: {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'status': self.printer_handler.get_status()
            }), 500

    
    def test_printer(self):
        """
        Print a test pattern.
        
        Returns:
            JSON with test result
        """
        try:
            success = self.printer_handler.test_print()
            
            if success:
                return jsonify({'success': True, 'message': 'Test print sent'}), 200
            else:
                return jsonify({'error': 'Test print failed'}), 500
                
        except Exception as e:
            logger.error(f"Error in test print: {e}")
            return jsonify({'error': str(e)}), 500


    def get_printer_protocol(self):
        """
        Get current printer protocol information.
        
        Returns:
            JSON with protocol details
        """
        status = self.printer_handler.get_status()
        return jsonify({
            'protocol': status.get('protocol', 'escpos'),
            'simulation_mode': status.get('simulation_mode', False)
        }), 200


    def switch_printer_protocol(self):
        """
        Switch printer protocol (ESC/POS or StarTSP).
        
        Expected JSON: {"protocol": "escpos" | "startsp"}
        
        Returns:
            JSON with switch result
        """
        try:
            data = request.json
            new_protocol = data.get('protocol')
            
            if not new_protocol:
                return jsonify({'error': 'Protocol not specified'}), 400
            
            if new_protocol not in ['escpos', 'startsp']:
                return jsonify({'error': 'Invalid protocol. Must be "escpos" or "startsp"'}), 400
            
            # Attempt to switch protocol
            success = self.printer_handler.switch_protocol(new_protocol)
            
            if success:
                return jsonify({
                    'success': True,
                    'protocol': new_protocol,
                    'message': f'Protocol switched to {new_protocol}'
                }), 200
            else:
                return jsonify({'error': 'Failed to switch protocol'}), 500
                
        except Exception as e:
            logger.error(f"Error switching protocol: {e}")
            return jsonify({'error': str(e)}), 500


    def scan_bluetooth(self):
        """
        Scan for nearby Bluetooth devices.
        
        Query Parameters:
            - timeout: Scan duration in seconds (default: 10)
        
        Returns:
            JSON with list of discovered devices
        """
        try:
            timeout = request.args.get('timeout', 10, type=int)
            
            # Limit timeout to reasonable range
            timeout = max(5, min(timeout, 30))
            
            devices = self.printer_handler.scan_bluetooth_devices(timeout)
            
            return jsonify({
                'success': True,
                'devices': devices,
                'count': len(devices)
            }), 200
            
        except Exception as e:
            logger.error(f"Error scanning Bluetooth: {e}")
            return jsonify({'error': str(e)}), 500


    def connect_bluetooth(self):
        """
        Connect to Bluetooth printer by MAC address.
        
        Body:
            - mac: Bluetooth MAC address (e.g., "AA:BB:CC:DD:EE:FF")
            - port: RFCOMM port (default: 1)
        
        Returns:
            JSON with connection status
        """
        try:
            data = request.get_json()
            logger.info(f"Bluetooth connect request received with data: {data}")
            
            mac = data.get('mac') if data else None
            port = data.get('port', 1) if data else 1
            
            if not mac:
                logger.error(f"Bluetooth connect attempt without MAC address. Received data: {data}")
                return jsonify({'success': False, 'error': 'MAC address required'}), 400
            
            logger.info(f"API: Bluetooth connect request for {mac}:{port}")
            
            # Disconnect current connection
            self.printer_handler.disconnect()
            
            logger.info(f"Attempting connection to Bluetooth printer {mac}...")
            success = self.printer_handler.connect_bluetooth(mac, port)
            
            if success:
                # Only save config if connection succeeded
                self.config['printer']['bluetooth_mac'] = mac
                self.config['printer']['bluetooth_port'] = port
                self.config['printer']['type'] = 'bluetooth'
                self.save_config()
                
                # Update handler config
                self.printer_handler.config = self.config
                
                logger.info(f"API: Bluetooth printer connected successfully: {mac}")
                return jsonify({
                    'success': True,
                    'status': self.printer_handler.get_status()
                }), 200
            else:
                # Connection failed - provide detailed error message
                error_msg = f'Connection to {mac} failed. '
                
                error_msg += 'Troubleshooting: '
                error_msg += '1) Ensure device is paired (bluetoothctl), '
                error_msg += '2) Check printer is powered on, '
                error_msg += '3) Verify printer is in range, '
                error_msg += '4) Try RFCOMM port 1 or 2. '
                error_msg += 'Check backend logs for details.'
                
                logger.error(f"API: Connection failed for {mac}. See detailed logs above.")
                return jsonify({
                    'success': False,
                    'error': error_msg
                }), 500
            
        except Exception as e:
            logger.error(f"API: Unexpected error in Bluetooth connect: {type(e).__name__}: {e}")
            logger.exception("Full traceback:")
            return jsonify({
                'success': False,
                'error': f'Unexpected error: {str(e)}. Check backend logs.'
            }), 500


    def disconnect_bluetooth(self):
        """
        Disconnect from currently connected Bluetooth printer.
        
        Returns:
            JSON with disconnection status
        """
        try:
            logger.info("API: Bluetooth disconnect request received")
            
            if not self.printer_handler.is_connected:
                logger.info("No active connection, returning success")
                return jsonify({
                    'success': True,
                    'message': 'No active connection'
                }), 200
            
            # Disconnect
            self.printer_handler.disconnect()
            
            logger.info("Bluetooth printer disconnected")
            return jsonify({
                'success': True,
                'message': 'Printer disconnected',
                'status': self.printer_handler.get_status()
            }), 200
            
        except Exception as e:
            logger.error(f"Error disconnecting Bluetooth printer: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500


    def unpair_bluetooth(self):
        """
        Unpair a Bluetooth device at OS level.
        
        Body:
            - mac: Bluetooth MAC address to unpair (optional, uses config if not provided)
        
        Returns:
            JSON with unpair status
        """
        try:
            # Handle both JSON and non-JSON requests
            data = request.get_json(silent=True) or {}
            mac = data.get('mac') or self.config['printer'].get('bluetooth_mac')
            
            if not mac:
                return jsonify({
                    'success': False,
                    'error': 'No MAC address provided or configured'
                }), 400
            
            logger.info(f"API: Bluetooth unpair request for {mac}")
            
            # Disconnect first if connected
            if self.printer_handler.is_connected and self.printer_handler.connection_type == 'bluetooth':
                self.printer_handler.disconnect()
            
            # Unpair at OS level using bluetoothctl
            import subprocess
            result = subprocess.run(
                ['bluetoothctl', 'remove', mac],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 or 'Device has been removed' in result.stdout:
                # Clear all Bluetooth config
                self.config['printer']['bluetooth_mac'] = None
                self.config['printer']['bluetooth_port'] = None
                # Reset to USB if Bluetooth was selected
                if self.config['printer']['type'] == 'bluetooth':
                    self.config['printer']['type'] = 'usb'
                self.save_config()
                
                # Update handler config
                self.printer_handler.config = self.config
                
                logger.info(f"Successfully unpaired device {mac} and reset config")
                return jsonify({
                    'success': True,
                    'message': f'Device {mac} unpaired successfully'
                }), 200
            else:
                logger.warning(f"Failed to unpair device {mac}: {result.stderr}")
                return jsonify({
                    'success': False,
                    'error': f'Failed to unpair: {result.stderr}',
                    'stdout': result.stdout
                }), 500
            
        except Exception as e:
            logger.error(f"Error unpairing Bluetooth device: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500


    def switch_connection(self):
        """
        Switch printer connection type.
        
        Body:
            - type: Connection type ('usb', 'bluetooth', or 'auto')
        
        Returns:
            JSON with new connection status
        """
        try:
            data = request.get_json()
            conn_type = data.get('type')
            
            if conn_type not in ['usb', 'bluetooth', 'auto']:
                return jsonify({
                    'success': False,
                    'error': 'Invalid type. Must be "usb", "bluetooth", or "auto"'
                }), 400
            
            # Disconnect current
            self.printer_handler.disconnect()
            
            self.config['printer']['type'] = conn_type
            
            self.save_config()
            
            # Update handler config
            self.printer_handler.config = self.config
            
            # Reconnect
            success = self.printer_handler.connect()
            
            logger.info(f"Switched connection type to: {conn_type}")
            return jsonify({
                'success': success,
                'connection_type': self.printer_handler.connection_type,
                'status': self.printer_handler.get_status()
            }), 200
            
        except Exception as e:
            logger.error(f"Error switching connection: {e}")
            return jsonify({'error': str(e)}), 500


    def get_gpio_status(self):
        """
        Get GPIO button status.
        
        Returns:
            JSON with GPIO status
        """
        return jsonify(self.gpio_handler.get_button_status()), 200


    def simulate_button(self, button_number):
        """
        Simulate a button press (for testing).
        
        Args:
            button_number: Button number to simulate (1-4)
            
        Returns:
            JSON with result
        """
        # NOTE: would be better to validate button_number against config...
        if button_number < 1 or button_number > 4:
            return jsonify({'error': 'Invalid button number'}), 400
        
        self.gpio_handler.simulate_button_press(button_number)
        return jsonify({'success': True, 'message': f'Simulated button {button_number} press'}), 200
