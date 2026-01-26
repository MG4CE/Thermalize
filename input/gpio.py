"""
GPIO button handler for Raspberry Pi.
Monitors physical buttons and triggering callback.
"""

import json
import logging

try:
    from gpiozero import Button # type: ignore
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: gpiozero not available. Running in simulation mode.")


logger = logging.getLogger(__name__)


class GPIOHandler:
    """Handle GPIO button monitoring and events."""
    
    def __init__(self, config_path='config.json', print_callback=None):
        """
        Initialize GPIO handler.
        
        Args:
            config_path: Path to configuration file
            print_callback: Function to call when button pressed, receives button number
        """
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        self.pins = self.config['gpio']['pins']
        self.bounce_time = self.config['gpio']['bounce_time'] / 1000.0  # Convert ms to seconds
        self.print_callback = print_callback
        self.buttons = []
        
        if not GPIO_AVAILABLE:
            logger.warning("GPIO not available. Running in simulation mode.")
            return
        
        # Setup GPIO
        self._setup_gpio()
    
    def _setup_gpio(self):
        """Configure GPIO buttons using gpiozero."""
        if not GPIO_AVAILABLE:
            return
        
        try:
            pud_str = self.config['gpio'].get('pull_up_down', 'pull_up')
            pull_up = (pud_str == 'pull_up')
            
            success_count = 0
            for idx, pin in enumerate(self.pins):
                btn_num = idx + 1
                try:
                    # Create Button with automatic debouncing
                    button = Button(
                        pin,
                        pull_up=pull_up,
                        bounce_time=self.bounce_time
                    )
                    
                    # Assign callback using closure with default argument
                    button.when_pressed = lambda b=btn_num: self._button_pressed(b)
                    
                    self.buttons.append(button)
                    success_count += 1
                    logger.info(f"GPIO pin {pin} (button {btn_num}) configured successfully")
                    
                except Exception as e:
                    logger.error(f"Pin {pin} setup failed: {e}")
            
            if success_count == 0:
                logger.warning("No GPIO pins configured successfully. Buttons will not work.")
            else:
                logger.info(f"GPIO setup complete: {success_count}/{len(self.pins)} pins configured")

        except Exception as e:
            logger.error(f"Fatal GPIO error: {e}")
    
    def _button_pressed(self, button_number: int):
        """
        Callback when button is pressed (called by GPIO event detection).
        
        Args:
            button_number: Button number (1-4)
        """
        logger.info(f"Button {button_number} pressed")
        
        if self.print_callback:
            try:
                self.print_callback(button_number)
            except Exception as e:
                logger.error(f"Error in print callback: {e}")
    
    def cleanup(self):
        """Cleanup GPIO resources."""
        if GPIO_AVAILABLE and self.buttons:
            try:
                for button in self.buttons:
                    button.close()
                self.buttons.clear()
                logger.info("GPIO cleanup complete")
            except Exception as e:
                logger.error(f"Error during GPIO cleanup: {e}")
    
    def get_button_status(self) -> dict:
        """
        Get status of all buttons.
        
        Returns:
            dict: Status of each button
        """
        if not GPIO_AVAILABLE:
            return {
                'available': False,
                'simulation_mode': True,
                'buttons': {}
            }
        
        try:
            button_states = {}
            for idx, button in enumerate(self.buttons):
                button_number = idx + 1
                button_states[button_number] = {
                    'pin': self.pins[idx],
                    'pressed': button.is_pressed
                }
            
            return {
                'available': True,
                'simulation_mode': False,
                'buttons': button_states
            }
        except Exception as e:
            logger.error(f"Error getting button status: {e}")
            return {
                'available': False,
                'error': str(e)
            }
    
    def simulate_button_press(self, button_number: int):
        """
        Simulate a button press (for testing without hardware).
        
        Args:
            button_number: Button number (1-4)
        """
        logger.info(f"Simulating button {button_number} press")
        if self.print_callback:
            try:
                self.print_callback(button_number)
            except Exception as e:
                logger.error(f"Error in simulated button callback: {e}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()
