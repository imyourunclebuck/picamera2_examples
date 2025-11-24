# Pico W Holiday Controller Bundle

This folder contains the files to copy directly to a Raspberry Pi Pico W running MicroPython. Upload the contents of this folder (especially `main.py`) to the board's root filesystem using `mpremote`, `ampy`, `rshell`, or Thonny.

## Files
- `main.py`: Complete controller firmware with embedded Midnight Dashboard UI.
- `config.json` (auto-created): Generated on first boot if missing; stores Wi-Fi, channels, scenes, and global settings.

## Upload example (mpremote)
```bash
mpremote connect COMx cp pico_bundle/main.py :main.py
```
Replace `COMx` with your Pico W serial port. After copying, reboot the board to start the access point and web server.
