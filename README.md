# RS232-KLine Bridge Suite

GUI for controlling and monitoring an RS232/K-Line bridge and its bootloader.

## Features

- Connect and disconnect from the serial bridge
- Select COM port and port baud rate
- Toggle DTR and monitor its state
- Send bridge commands for configuration and reset
- Read and save bridge parameters
- View bridge statistics and runtime counters
- Send terminal payloads in string, character, or hex mode
- Control bootloader actions:
	- Connect to bootloader
	- Select firmware file
	- Select EEPROM file
	- Flash firmware or EEPROM
	- Start application from bootloader
- Persist app settings in `app_config.json`

## Requirements

- Python 3.11 or newer
- `customtkinter`
- `pyserial`

## Run

```bash
python bridge_suite.py
```

The packaged Windows executable is created in `dist/` as `RS232-KLine Bridge Suite.exe`.

## Command Reference

### Configuration tab

- `Refresh` — reload available COM ports
- `Connect` — open or close the serial connection
- `Reset` — send the bridge reset command
- `⭱` — read current bridge parameters
- `⭳` — save bridge parameters permanently

### Statistics tab

- `⭱` — refresh bridge statistics
- `⌫` — clear runtime statistics values

### Bootloader tab

- `Connect to Bootloader` — reset the bridge and enter bootloader mode
- `Select File` — choose a firmware or EEPROM file
- `Flash Firmware` — program the selected firmware file
- `Flash EEPROM` — program the selected EEPROM file
- `Start Application` — exit bootloader and start the application

### Terminal tab

- `Send` — transmit the current payload
- `String`, `Character`, `Bytes (Hex)` — select the terminal input mode

## Screenshots

Add screenshots of the main tabs here if desired. The UI currently includes:

- Configuration
- Statistics
- Terminal
- Bootloader

## Configuration

The application stores its settings in `app_config.json` next to `app.py`.

Saved values include:

- UI mode
- COM port baud rate
- RS232 baud rate
- Debug logging flag
- Last connected COM port

## Tabs

### Configuration

Shows bridge parameters such as buffer sizes, baud rates, DTR forwarding, and buffer usage.

### Statistics

Shows runtime counters and bridge metric/error counters.

### Terminal

Used to send raw payloads to the bridge in different formats.

### Bootloader

Used to connect to the bootloader and flash firmware or EEPROM files.

## Notes

- The app validates the bridge identity during connection.
- Debug output can be enabled or disabled from the View menu.
- If the connected device does not respond as expected, the app disconnects automatically.
