#!/bin/bash

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="thermal-printer.service"
VENV_DIR="$SCRIPT_DIR/venv"

# Check if running with root privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR]${NC} This script requires root privileges."
    echo -e "${YELLOW}[INFO]${NC} Please run with: sudo $0 $*"
    exit 1
fi

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Initial setup function for the project
setup_app() {
    print_info "Starting Thermalize setup..."
    
    # 1. Create a virtual environment
    print_info "Creating virtual environment..."
    if [ -d "$VENV_DIR" ]; then
        print_warning "Virtual environment already exists. Skipping creation."
    else
        python3 -m venv "$VENV_DIR"
        if [ $? -ne 0 ]; then
            print_error "Failed to create virtual environment"
            exit 1
        fi
        print_info "Virtual environment created successfully"
    fi
    
    # 2. Activate the virtual environment
    print_info "Activating virtual environment..."
    source "$VENV_DIR/bin/activate"
    
    # 3. Install the required packages
    print_info "Installing required packages..."
    # Detect if running on Raspberry Pi
    if [ -f "/proc/device-tree/model" ] && grep -q "Raspberry Pi" /proc/device-tree/model; then
        print_info "Detected Raspberry Pi, using requirements_rpi.txt"
        REQUIREMENTS_FILE="requirements_rpi.txt"
    else
        print_info "Using standard requirements.txt"
        REQUIREMENTS_FILE="requirements.txt"
    fi
    
    if [ -f "$SCRIPT_DIR/$REQUIREMENTS_FILE" ]; then
        pip install --upgrade pip
        pip install -r "$SCRIPT_DIR/$REQUIREMENTS_FILE"
        if [ $? -ne 0 ]; then
            print_error "Failed to install packages"
            exit 1
        fi
        print_info "Packages installed successfully"
    else
        print_error "Requirements file not found: $REQUIREMENTS_FILE"
        exit 1
    fi
    
    # 4. Create necessary directories and files
    print_info "Creating necessary directories..."
    mkdir -p "$SCRIPT_DIR/uploads"
    mkdir -p "$SCRIPT_DIR/processed"
    mkdir -p "$SCRIPT_DIR/static"
    
    if [ ! -f "$SCRIPT_DIR/images_db.json" ]; then
        echo "[]" > "$SCRIPT_DIR/images_db.json"
        print_info "Created images_db.json"
    fi
    
    # 5. Configure config.json through user input
    print_info "Configuring application settings..."
    echo ""
    read -p "Enter server host (default: 0.0.0.0): " host
    host=${host:-0.0.0.0}
    
    read -p "Enter server port (default: 5000): " port
    port=${port:-5000}
    
    read -p "Enter max image width in pixels (default: 600): " max_width
    max_width=${max_width:-600}
    
    read -p "Enter paper width in mm (default: 83): " paper_width
    paper_width=${paper_width:-83}
    
    # Update config.json with user input
    python3 - <<EOF
import json

config_path = "$SCRIPT_DIR/config.json"
with open(config_path, 'r') as f:
    config = json.load(f)

config['server']['host'] = "$host"
config['server']['port'] = int("$port")
config['image_settings']['max_width'] = int("$max_width")
config['image_settings']['paper_width_mm'] = int("$paper_width")

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print("Configuration updated successfully")
EOF
    
    # 6. Create and activate service
    print_info "Creating systemd service..."
    
    # Get current user
    CURRENT_USER=$(whoami)
    
    # Create service file
    SERVICE_CONTENT="[Unit]
Description=Thermal Photo Printer Web Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
Environment=\"PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin\"
ExecStart=$VENV_DIR/bin/python3 app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target"
    
    echo "$SERVICE_CONTENT" | tee "/etc/systemd/system/$SERVICE_NAME" > /dev/null
    
    # Reload systemd, enable and start service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
    
    if [ $? -eq 0 ]; then
        print_info "Service installed and started successfully"
        print_info "You can check the status with: sudo systemctl status $SERVICE_NAME"
    else
        print_error "Failed to start service"
        exit 1
    fi
    
    echo ""
    print_info "Setup completed successfully!"
    print_info "Access the application at http://$host:$port"
}

# Show application logs function
show_logs() {
    print_info "Showing application logs (press Ctrl+C to exit)..."
    journalctl -u "$SERVICE_NAME" -f
}

# App uninstall function
uninstall_app() {
    print_warning "This will uninstall the Thermalize application"
    read -p "Are you sure you want to continue? (yes/no): " confirm
    
    if [ "$confirm" != "yes" ]; then
        print_info "Uninstall cancelled"
        exit 0
    fi
    
    # 1. Stop and disable the service
    print_info "Stopping and disabling service..."
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl stop "$SERVICE_NAME"
    fi
    
    if systemctl is-enabled --quiet "$SERVICE_NAME"; then
        systemctl disable "$SERVICE_NAME"
    fi
    
    # 2. Remove the service file
    print_info "Removing service file..."
    if [ -f "/etc/systemd/system/$SERVICE_NAME" ]; then
        rm "/etc/systemd/system/$SERVICE_NAME"
        systemctl daemon-reload
        print_info "Service file removed"
    fi
    
    # 3. Remove the virtual environment
    print_info "Removing virtual environment..."
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        print_info "Virtual environment removed"
    fi
    
    print_info "Uninstall completed successfully!"
    print_warning "Note: User data (uploads, processed images, config) has been preserved"
    print_info "To remove all data, manually delete: $SCRIPT_DIR"
}

# Show status of the service
show_status() {
    print_info "Checking service status..."
    systemctl status "$SERVICE_NAME"
}

# Restart the service
restart_service() {
    print_info "Restarting service..."
    systemctl restart "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        print_info "Service restarted successfully"
    else
        print_error "Failed to restart service"
        exit 1
    fi
}

# Main function to handle user input and call appropriate functions
main() {
    case "$1" in
        setup|install)
            setup_app
            ;;
        logs)
            show_logs
            ;;
        uninstall|remove)
            uninstall_app
            ;;
        status)
            show_status
            ;;
        restart)
            restart_service
            ;;
        *)
            echo "Thermalize Setup Script"
            echo ""
            echo "Usage: $0 {setup|logs|uninstall|status|restart}"
            echo ""
            echo "Commands:"
            echo "  setup      - Initial setup and installation"
            echo "  logs       - Show application logs (live)"
            echo "  status     - Show service status"
            echo "  restart    - Restart the service"
            echo "  uninstall  - Uninstall the application"
            echo ""
            exit 1
            ;;
    esac
}

# Parse command line arguments and call main function
main "$@"
