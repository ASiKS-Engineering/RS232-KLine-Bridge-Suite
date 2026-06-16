# RS232-KLine Bridge GUI

Professionelle Python-GUI fuer eine RS232-KLine-Bridge mit folgenden Funktionen:

- chip45boot2 Bootloader-Integration
- Bridge-Kommandos:
  - `-v` Get version
  - `-c` Get current configuration
  - `-r` Force adapter reset
  - `-n` Get bridge statistics
  - `-s` Save current config to EEPROM
  - `-rrx` Set RS232 RX buffer size
  - `-rtx` Set RS232 TX buffer size
  - `-rbr` Set RS232 baud rate
  - `-krx` Set KLine RX buffer size
  - `-ktx` Set KLine TX buffer size
  - `-kbr` Set KLine baud rate
  - `-fwd` Set DTR forwarding
- DTR kann ein-/ausgeschaltet werden
- Dropdowns fuer:
  - Buffer-Parameter als Dezimalwerte mit Einheit `Bytes`: `16, 32, 64, 128, 256, 512, 1024`
  - Baudraten (`-rbr`, `-kbr` inklusive KLine-Baudrate und Verbindungs-Baudrate)
  - DTR Forwarding (`-fwd`)
- Header-Baudrate ist ausschliesslich fuer den Verbindungsaufbau mit dem seriellen Port
- `RS232 Baud (-rbr)` und `KLine Baud (-kbr)` werden ueber die Parameter-Kommandos gesetzt
- Buffer-Werte werden zusaetzlich auf gueltiges 16-Bit-Zweierkomplement geprueft
- Alle Bridge-Kommandos werden nur bei aktivem DTR gesendet
- Antwortdaten werden im Log-Fenster angezeigt
- Bootloader-Workflow:
  - `Connect to Bootloader`: sendet zuerst `-r`, trennt danach die Bridge-Verbindung und wechselt in den Bootloader-Modus
  - separate Dateiauswahl + Flash-Buttons fuer `Flash Firmware` und `Flash EEPROM`
  - `Start Application` sendet `g` an den Bootloader
  - Flash-Fortschritt ueber Progressbar
- Hell-/Dunkelmodus automatisch nach OS-Einstellung (via CustomTkinter `system`)

## Start

```powershell
cd C:\Users\q259338\source\kline_bridge_gui
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Hinweise

- Die GUI nutzt das chip45boot2-Protokoll nativ in Python (kein externer EXE-Aufruf).
- Intel-HEX-Dateien fuer Firmware/EEPROM werden geparst, in 16-Byte-Records aufbereitet und via XON/XOFF uebertragen.
