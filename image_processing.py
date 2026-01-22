"""
Image processing module implementing various dithering algorithms.
"""

from PIL import Image # type: ignore

class DitheringMethod:
    FLOYD_STEINBERG = 'floyd_steinberg'
    ATKINSON = 'atkinson'
    ORDERED = 'ordered'
    CLUSTERED_DOT = 'clustered_dot'
    THRESHOLD = 'threshold'
    NONE = 'none'

class ImageProcessor:

    def apply_dithering(self, img: Image.Image, method: DitheringMethod) -> Image.Image:
        """
        Apply the specified dithering method to a grayscale image.
        
        Args:
            img: Grayscale PIL Image
            method: Dithering method to apply
            
        Returns:
            PIL Image in mode '1' (1-bit pixels, black and white)
        """
        if method == DitheringMethod.FLOYD_STEINBERG:
            return self._floyd_steinberg_dither(img)
        elif method == DitheringMethod.ATKINSON:
            return self._atkinson_dither(img)
        elif method == DitheringMethod.ORDERED:
            return self._ordered_dither(img)
        elif method == DitheringMethod.CLUSTERED_DOT:
            return self._clustered_dot_dither(img)
        elif method == DitheringMethod.THRESHOLD:
            return self.threshold_dither(img)
        elif method == DitheringMethod.NONE:
            return self._none(img)
        else:
            raise ValueError(f"Unknown dithering method: {method}")

    def _none(self, img: Image.Image) -> Image.Image:
        """
        No dithering; convert image to 1-bit without dithering.
        
        Args:
            img: Grayscale PIL Image
        """
        return img.convert('1', dither=Image.Dither.NONE)

    def _threshold_dither(self, img: Image.Image) -> Image.Image:
        """
        Apply simple threshold dithering (no dithering).
        Pixels above 128 are set to white, below to black.
        
        Args:
            img: Grayscale PIL Image
            
        Returns:
            PIL Image in mode '1'
        """
        return img.convert('1', dither=Image.Dither.NONE)

    def _floyd_steinberg_dither(self, img: Image.Image) -> Image.Image:
        """
        Apply Floyd-Steinberg dithering to a grayscale image.
        This algorithm distributes quantization error to neighboring pixels.
        
        Args:
            img: Grayscale PIL Image
        """
        return img.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
    
    def _atkinson_dither(self, img: Image.Image) -> Image.Image:
        """
        Apply Atkinson dithering (used in early Macintosh).
        Produces lighter images with less contrast than Floyd-Steinberg.
        
        Args:
            img: Grayscale PIL Image
            
        Returns:
            PIL Image in mode '1'
        """
        pixels = list(img.getdata())
        width, height = img.size
        
        # Create mutable pixel array
        pixel_array = [[pixels[y * width + x] for x in range(width)] for y in range(height)]
        
        for y in range(height):
            for x in range(width):
                old_pixel = pixel_array[y][x]
                new_pixel = 255 if old_pixel > 127 else 0
                pixel_array[y][x] = new_pixel
                
                error = (old_pixel - new_pixel) // 8  # Divide by 8 for Atkinson
                
                # Distribute error to neighboring pixels
                if x + 1 < width:
                    pixel_array[y][x + 1] = min(255, max(0, pixel_array[y][x + 1] + error))
                if x + 2 < width:
                    pixel_array[y][x + 2] = min(255, max(0, pixel_array[y][x + 2] + error))
                if y + 1 < height:
                    if x > 0:
                        pixel_array[y + 1][x - 1] = min(255, max(0, pixel_array[y + 1][x - 1] + error))
                    pixel_array[y + 1][x] = min(255, max(0, pixel_array[y + 1][x] + error))
                    if x + 1 < width:
                        pixel_array[y + 1][x + 1] = min(255, max(0, pixel_array[y + 1][x + 1] + error))
                if y + 2 < height:
                    pixel_array[y + 2][x] = min(255, max(0, pixel_array[y + 2][x] + error))
        
        # Convert back to image
        dithered_pixels = [pixel_array[y][x] for y in range(height) for x in range(width)]
        dithered_img = Image.new('L', (width, height))
        dithered_img.putdata(dithered_pixels)
        
        return dithered_img.convert('1', dither=Image.Dither.NONE)
    
    def _ordered_dither(self, img: Image.Image) -> Image.Image:
        """
        Apply ordered dithering using Bayer matrix.
        Creates a distinctive crosshatch pattern.
        
        Args:
            img: Grayscale PIL Image
            
        Returns:
            PIL Image in mode '1'
        """
        # 4x4 Bayer matrix
        bayer_matrix = [
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5]
        ]
        
        pixels = list(img.getdata())
        width, height = img.size
        
        dithered_pixels = []
        for y in range(height):
            for x in range(width):
                old_pixel = pixels[y * width + x]
                threshold = (bayer_matrix[y % 4][x % 4] / 16.0) * 255
                new_pixel = 255 if old_pixel > threshold else 0
                dithered_pixels.append(new_pixel)
        
        dithered_img = Image.new('L', (width, height))
        dithered_img.putdata(dithered_pixels)
        
        return dithered_img.convert('1', dither=Image.Dither.NONE)
    
    def _clustered_dot_dither(self, img: Image.Image) -> Image.Image:
        """
        Apply clustered dot ordered dithering.
        Creates smooth halftone dots similar to newspaper printing.
        Based on a 4x4 Bayer-like matrix optimized for dot clustering.
        
        Args:
            img: Grayscale PIL Image
            
        Returns:
            PIL Image in mode '1'
        """
        # Clustered dot 4x4 matrix (values scaled for 0-255 comparison)
        # This specific matrix creates dot patterns that cluster together
        bayer_clustered = [
            [15, 135, 45, 165],
            [195, 75, 225, 105],
            [60, 180, 30, 150],
            [240, 120, 210, 90]
        ]
        
        pixels = list(img.getdata())
        width, height = img.size
        
        dithered_pixels = []
        for y in range(height):
            for x in range(width):
                pixel = pixels[y * width + x]
                threshold = bayer_clustered[y % 4][x % 4]
                
                # Inverted logic as per the C implementation:
                # pixel <= threshold means black (0), else white (255)
                # This creates the clustered dot effect
                new_pixel = 0 if pixel <= threshold else 255
                dithered_pixels.append(new_pixel)
        
        dithered_img = Image.new('L', (width, height))
        dithered_img.putdata(dithered_pixels)
        
        return dithered_img.convert('1', dither=Image.Dither.NONE)
