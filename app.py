"""
Main Flask application for Raspberry Pi Thermal Photo Printer.
"""

import os
import json
import logging

from image.handler import ImageHandler
from printer.manager import PrinterManager
from input.gpio import GPIOHandler

from api.router import Router

log_level = logging.DEBUG

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Ensure this overrides any prior configuration
)

logger = logging.getLogger(__name__)
logger.info(f"Logging level set to: {logging.getLevelName(log_level)}")

CONFIG_PATH = 'config.json'
IMAGES_DB_PATH = 'images_db.json'

images_db = {}

# Load or initialize image database
if os.path.exists(IMAGES_DB_PATH):
    with open(IMAGES_DB_PATH, 'r') as f:
        loaded_db = json.load(f)
        # Ensure it's a dictionary
        if isinstance(loaded_db, dict):
            images_db = loaded_db
        elif isinstance(loaded_db, list):
            logger.warning("images_db.json contains a list instead of dict, converting...")
            # Convert list to dict using 'id' as key
            for item in loaded_db:
                if isinstance(item, dict) and 'id' in item:
                    images_db[item['id']] = item
            # Save the corrected database
            with open(IMAGES_DB_PATH, 'w') as out_f:
                json.dump(images_db, out_f, indent=2)
        else:
            logger.error(f"images_db.json has unexpected type: {type(loaded_db)}, using empty dict")
            images_db = {}

# Initialize handlers inside main block to ensure proper logging
image_handler = None
printer_handler = None

def button_press_callback(button_number):
    """
    Callback function for GPIO button press events.
    
    Args:
        button_number: Button number that was pressed (1-4)
    """
    logger.info(f"Button {button_number} press callback triggered")
    
    # Load current config to get button assignments
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
    
    # Get assigned image ID for this button
    image_id = config['button_assignments'].get(str(button_number))
    
    if not image_id:
        logger.warning(f"Button {button_number} has no image assigned")
        return
    
    if image_id not in images_db:
        logger.error(f"Image {image_id} not found in database")
        return
    
    # Get processed image path
    processed_path = image_handler.get_processed_image(image_id)
    
    if not processed_path or not os.path.exists(processed_path):
        logger.error(f"Processed image not found for {image_id}")
        return
    
    # Print the image
    logger.info(f"Printing image {image_id} from button {button_number}")
    success = printer_handler.print_image(processed_path)
    
    if success:
        logger.info(f"Successfully printed image {image_id}")
    else:
        logger.error(f"Failed to print image {image_id}")

if __name__ == '__main__':
    try:
        # Initialize handlers
        logger.info("Initializing handlers...")
        gpio_handler = GPIOHandler(CONFIG_PATH, print_callback=button_press_callback)
        image_handler = ImageHandler(CONFIG_PATH)
        printer_handler = PrinterManager(CONFIG_PATH)
        
        # Load configuration
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)

        router = Router(
            image_handler=image_handler,
            printer_handler=printer_handler,
            gpio_handler=gpio_handler,
            config_path=CONFIG_PATH,
            image_db=images_db,
            images_db_path=IMAGES_DB_PATH
        )
        
        host = config['server']['host']
        port = config['server']['port']
        debug = config['server']['debug']
        
        logger.info(f"Starting server on {host}:{port}")
        logger.info(f"Recommended image width: {image_handler.get_recommended_width()}px")
        logger.info(f"Paper size: {image_handler.get_paper_width_mm()}mm")
        
        router.app.run(host=host, port=port, debug=debug, use_reloader=False)
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        gpio_handler.cleanup()
        logger.info("Shutting down application...")
        printer_handler.disconnect()
        logger.info("Cleanup complete")
