"""
Custom exceptions for printer operations.
"""


class PrinterError(Exception):
    """Base exception for all printer-related errors."""
    
    def __init__(self, message: str, context: dict = None):
        """
        Initialize printer error.
        
        Args:
            message: Error message
            context: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.context = context or {}
    
    def __str__(self):
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} ({context_str})"
        return self.message


class PrinterConnectionError(PrinterError):
    """Raised when printer connection fails."""
    pass


class USBConnectionError(PrinterConnectionError):
    """Raised when USB printer connection fails."""
    pass


class BluetoothPairingError(PrinterConnectionError):
    """Raised when Bluetooth device pairing fails."""
    pass


class PrinterNotFoundError(PrinterError):
    """Raised when no printer device is found."""
    pass


class InvalidConfigurationError(PrinterError):
    """Raised when printer configuration is invalid."""
    pass
