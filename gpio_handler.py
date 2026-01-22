"""
GPIO button handler for Raspberry Pi.
Monitors physical buttons and triggering callback.
"""

import json
import logging

try:
    import RPi.GPIO as GPIO # type: ignore
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: RPi.GPIO not available. Running in simulation mode.")


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
        self.bounce_time = self.config['gpio']['bounce_time']
        self.print_callback = print_callback
        
        if not GPIO_AVAILABLE:
            logger.warning("GPIO not available. Running in simulation mode.")
            return
        
        # Setup GPIO
        self._setup_gpio()
    
    def _setup_gpio(self):
        """Configure GPIO pins."""
        if not GPIO_AVAILABLE:
            return
        
        try:
            # Use BCM pin numbering
            GPIO.setmode(GPIO.BCM)
            
            # Setup each pin
            pull_up_down = self.config['gpio'].get('pull_up_down', 'pull_up')
            pud = GPIO.PUD_UP if pull_up_down == 'pull_up' else GPIO.PUD_DOWN
            
            for idx, pin in enumerate(self.pins):
                button_number = idx + 1
                GPIO.setup(pin, GPIO.IN, pull_up_down=pud)
                
                # Add event detection for button press (falling edge when pulled up)
                GPIO.add_event_detect(
                    pin,
                    GPIO.FALLING,
                    callback=lambda channel, btn=button_number: self._button_pressed(btn),
                    bouncetime=self.bounce_time
                )
                logger.info(f"GPIO pin {pin} configured with {pull_up_down} and event detection")
            
            logger.info(f"GPIO setup complete for pins: {self.pins}")
            
        except Exception as e:
            logger.error(f"Error setting up GPIO: {e}")
    
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
        if GPIO_AVAILABLE:
            try:
                # Remove event detection before cleanup
                for pin in self.pins:
                    GPIO.remove_event_detect(pin)
                GPIO.cleanup()
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
            for idx, pin in enumerate(self.pins):
                button_number = idx + 1
                state = GPIO.input(pin)
                button_states[button_number] = {
                    'pin': pin,
                    'pressed': state == GPIO.LOW
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
