# Thermalize

A web-based application that allows you to print images on thermal receipt printers.

## Features

- **Web Interface**: Upload and manage images through a locally hosted web UI

- **Image Processing**: Provides automatic width adjustment and various dithering options for optimal thermal printing image quality

- **GPIO Integration**: Supports physical buttons connected to Raspberry Pi GPIO pins to trigger image printing

- **Virtual Receipt Preview**: See exactly how your image will print in black & white

- **USB and Bluetooth Printer Support**: Works with USB and Bluetooth thermal receipt printers

- **Compatible with ESC/POS and Star TSP Printers**: Works with generic ESC/POS printers and Star TSP series printers


## Installation (Setup Script)

### 1. Clone or download the repository:

```bash
git clone https://github.com/MG4CE/thermalize.git
cd thermalize
```

### 2. Run the setup script:

```bash
sudo ./setup.sh
```

## Manual Installation

### 1. Clone or download the repository:

```bash
git clone https://github.com/MG4CE/thermalize.git
cd thermalize
```

### 2. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies:

```bash
pip install -r requirements.txt
```

### 4. Configure the application:

Edit the `config.json` file to set up printer settings, GPIO pins, and other preferences.

### 5. Start the application:

```bash
python app.py
```

## Service Setup (Optional)
To run Thermalize as a service a Linux system, you can use the provided `setup.sh` script:

```bash
sudo ./setup.sh --service-setup
```

## Usage

Access the web interface by navigating to `http://<device-ip>:5000` in your web browser. From there, you can upload images, assign them to GPIO buttons, and manage printing options.


## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
