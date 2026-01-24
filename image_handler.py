"""
Image processing module for thermal printer.
Handles image conversion, resizing, dithering, and positioning.
"""

import os
import json
from PIL import Image, ImageOps # type: ignore
import uuid
from typing import Tuple, Optional
from image_processing import ImageProcessor, DitheringMethod


class ImageHandler:
    """Process images for thermal printer output."""

    UPLOADS_DIR = 'uploads'
    PROCESSED_DIR = 'processed'
    
    def __init__(self, config_path='config.json'):
        """Initialize image processor with configuration."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        self.max_width = self.config['image_settings']['max_width']
        self.image_processor = ImageProcessor()
        
        # Ensure directories exist
        os.makedirs(self.UPLOADS_DIR, exist_ok=True)
        os.makedirs(self.PROCESSED_DIR, exist_ok=True)

    def save_uploaded_image(self, image_file) -> dict:
        """
        Save uploaded image and return metadata.
        
        Args:
            image_file: File object
            
        Returns:
            dict: Image metadata including id, filename, dimensions
        """
        # Generate unique ID
        image_id = str(uuid.uuid4())
        
        # Get original filename and extension
        original_filename = image_file.filename
        ext = os.path.splitext(original_filename)[1].lower()
        
        # Save original image
        filepath = os.path.join(self.UPLOADS_DIR, f"{image_id}{ext}")
        image_file.save(filepath)
        
        # Fix orientation (EXIF) and get dimensions
        with Image.open(filepath) as img:
            # Apply EXIF rotation
            img = ImageOps.exif_transpose(img)
            # overwrite with fixed orientation
            img.save(filepath)
            width, height = img.size
        
        metadata = {
            'id': image_id,
            'filename': original_filename,
            'filepath': filepath,
            'width': width,
            'height': height,
            'extension': ext,
            'processed': False,
            'position': {'x': 0, 'y': 0},
            'auto_fit': True
        }
        
        return metadata

    def _resize_image(self, img: Image.Image, target_width: int) -> Image.Image:
        """Resize image to target width while maintaining aspect ratio."""
        aspect_ratio = img.height / img.width
        new_width = self.max_width
        new_height = int(new_width * aspect_ratio)
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def _center_image(self, img: Image.Image, canvas_width: int) -> int:
        """Center image on a canvas of specified width."""
        if img.width < self.max_width:
             x_offset = (self.max_width - img.width) // 2
        else:
            x_offset = 0
        return x_offset

    def _process_raw_image(self, img: Image.Image, image_id: str) -> Tuple[str, int, int]:
        """
        Process image in RAW mode: resize if needed, keep original color/mode.
        
        Args:
            img: PIL Image object
            image_id: Unique image identifier
            
        Returns:
            str: Path to processed image
        """
        # Delete any existing normal processed version to avoid conflicts
        normal_path = os.path.join(self.PROCESSED_DIR, f"{image_id}.png")
        if os.path.exists(normal_path):
            os.remove(normal_path)
        
        # Keep original color/mode and Only resize if too wide
        if img.width > self.max_width:
            img = self._resize_image(img, self.max_width)
        
        # Save as-is (keeping color)
        processed_path = os.path.join(self.PROCESSED_DIR, f"{image_id}_raw.png")
        img.save(processed_path)

        return processed_path, img.width, img.height

    # TODO: might want move the offset handling to the printer handler instead
    def process_image(self, image_id: str, user_x_offset: int = 0, user_y_offset: int = 0, 
                     auto_fit: bool = True, dither_method: Optional[str] = None, 
                     raw_mode: bool = False) -> Tuple[str, int, int]:
        """
        Process image for thermal printing.
        
        Steps:
        1. Convert to grayscale (unless raw_mode=True)
        2. Resize to fit max_width
        3. Apply dithering
        4. Position on canvas with offsets
        
        Args:
            image_id: Unique image identifier
            user_x_offset: Horizontal offset in pixels
            user_y_offset: Vertical offset in pixels
            auto_fit: If True, auto-center and fit to width
            dither_method: Dithering method to use If None, uses FLOYD_STEINBERG.
            raw_mode: If True, skip all processing and keep image in original color/format
            
        Returns:
            tuple: (processed_filepath, final_width, final_height)
        """
        x_offset = 0
        y_offset = 0

        # Find original image
        original_path = None
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            path = os.path.join(self.UPLOADS_DIR, f"{image_id}{ext}")
            if os.path.exists(path):
                original_path = path
                break
        
        if not original_path:
            raise FileNotFoundError(f"Image {image_id} not found")
        
        # Load image
        img = Image.open(original_path)
        
        # RAW MODE: Skip all processing, just resize if needed
        if raw_mode:
            return self._process_raw_image(img, image_id)
        
        # NORMAL MODE: Apply dither processing
        # Delete any existing raw version to avoid conflicts
        raw_path = os.path.join(self.PROCESSED_DIR, f"{image_id}_raw.png")
        if os.path.exists(raw_path):
            os.remove(raw_path)

        # Convert to grayscale
        if img.mode != 'L':
            img = img.convert('L')
        
        if auto_fit:
            # Resize to fit max_width while maintaining aspect ratio
            if img.width > self.max_width:
                img = self._resize_image(img, self.max_width)
            
            # Center the image
            if img.width < self.max_width:
                x_offset = (self.max_width - img.width) // 2

        x_offset += user_x_offset
        y_offset += user_y_offset
        
        # Apply image processing/dithering
        selected_dither_method = dither_method 
        if dither_method:
            selected_dither_method = dither_method 
        else:
            selected_dither_method = self.config['image_settings'].get('dither_method', DitheringMethod.FLOYD_STEINBERG)

        img_dithered = self.image_processor.apply_dithering(img, selected_dither_method)
        
        # Create canvas with proper width
        canvas_width = self.max_width
        canvas_height = img_dithered.height + y_offset
        
        # Create white canvas
        canvas = Image.new('1', (canvas_width, canvas_height), 1)  # 1 = white
        
        # Paste dithered image onto canvas at specified position
        canvas.paste(img_dithered, (x_offset, y_offset))
        
        # Save processed image
        processed_path = os.path.join(self.PROCESSED_DIR, f"{image_id}.png")
        canvas.save(processed_path)
        
        return processed_path, canvas_width, canvas_height

    def get_processed_image(self, image_id: str) -> Optional[str]:
        """
        Get path to processed image.
        Checks for both raw and normal processed versions.
        
        Args:
            image_id: Unique image identifier
            
        Returns:
            str: Path to processed image or None if not found
        """
        # Check for raw processed version first
        raw_path = os.path.join(self.PROCESSED_DIR, f"{image_id}_raw.png")
        if os.path.exists(raw_path):
            return raw_path
        
        # Check for normal processed version
        processed_path = os.path.join(self.PROCESSED_DIR, f"{image_id}.png")
        if os.path.exists(processed_path):
            return processed_path
        
        return None

    def delete_image(self, image_id: str) -> bool:
        """
        Delete original and processed images.
        
        Args:
            image_id: Unique image identifier
            
        Returns:
            bool: True if successful
        """
        deleted = False
        
        # Delete original
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            path = os.path.join(self.UPLOADS_DIR, f"{image_id}{ext}")
            if os.path.exists(path):
                os.remove(path)
                deleted = True
        
        # Delete processed
        processed_path = os.path.join(self.PROCESSED_DIR, f"{image_id}.png")
        if os.path.exists(processed_path):
            os.remove(processed_path)
            deleted = True
        
        # Delete raw processed version
        raw_path = os.path.join(self.PROCESSED_DIR, f"{image_id}_raw.png")
        if os.path.exists(raw_path):
            os.remove(raw_path)
            deleted = True
        
        return deleted

    def get_recommended_width(self) -> int:
        """Get recommended image width in pixels."""
        return self.max_width

    def get_paper_width_mm(self) -> int:
        """Get paper width in millimeters."""
        return self.config['image_settings']['paper_width_mm']
