# Bluetooth Media Bridge

Stream audio from your iPhone (or any A2DP source) to your PC over Bluetooth — with metadata, cover art, and media key control.

Built on [BTstack](https://github.com/bluekitchen/btstack) + Python/PySide6.

## How It Works

```
iPhone  ──A2DP──►  bt_bridge.exe  ──TCP/IPC──►  Python GUI
         AVRCP                      JSON events    (tray app)
```

`bt_bridge.exe` runs BTstack as a Bluetooth A2DP sink, decodes audio (AAC or SBC), and streams events to the Python GUI over a local TCP socket.

## Requirements

### Hardware
- **Realtek-chipset USB Bluetooth dongle** (tested: TP-Link UB500, RTL8761B)
- Other chipsets are untested and likely unsupported

### Software
- Windows 10/11
- [MSYS2](https://www.msys2.org/) (MinGW64)
- [Zadig](https://zadig.akeo.ie/) — to replace the dongle driver with WinUSB
- Python 3.11+

## Setup

### 1. Replace Dongle Driver (Zadig)

1. Plug in your Bluetooth dongle
2. Open Zadig → Options → List All Devices
3. Select your dongle → Install **WinUSB** driver

> ⚠️ This replaces the default Windows BT driver. Your dongle will no longer work with Windows Bluetooth settings while WinUSB is active.

### 2. Install MSYS2 Dependencies

```bash
pacman -S mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja \
          mingw-w64-x86_64-portaudio mingw-w64-x86_64-fdk-aac
```

### 3. Clone

```bash
git clone --recurse-submodules https://github.com/yourname/bluetooth-media-bridge
cd bluetooth-media-bridge
```

### 4. Build bt_bridge.exe

Open MSYS2 MinGW64 terminal:

```bash
cd bluetooth_bridge/btstack/port/windows-winusb
mkdir build && cd build
cmake .. -G "Ninja"
ninja bt_bridge -j$(nproc)
```

### 5. Install Python Dependencies

```bash
pip install -r app/requirements.txt
```


## Running

```bash
python -m app.main
```

A tray icon will appear. The bridge connects to your iPhone automatically if previously paired.

> **Note:** Run from an MSYS2 MinGW64 terminal, or ensure `C:\msys64\mingw64\bin` is in your system PATH. Running from VSCode or cmd without this PATH will cause bt_bridge.exe to fail on startup.


## Features

| Feature | Status |
|---------|--------|
| AAC audio (iPhone preferred codec) | ✅ |
| SBC audio fallback | ✅ |
| AVRCP metadata (title, artist, album) | ✅ |
| Cover art download | ✅ |
| Media key control (play/pause/next/prev) | ✅ |
| Windows SMTC integration (taskbar media card) | ✅ |
| Auto-reconnect to last device | ✅ |
| System tray app | ✅ |
| Single instance enforcement | ✅ |

## Compatibility

### Bluetooth Dongles

| Chipset | Example | Support |
|---------|---------|---------|
| Realtek RTL8761B/BU | TP-Link UB500, ASUS BT500 | ✅ Tested |
| Other Realtek | Various | ⚠️ Likely works, untested |
| Intel / CSR / Broadcom / MediaTek | Various | ❌ Not supported |

### Source Devices

| Device | Audio | Metadata | Cover Art |
|--------|-------|----------|-----------|
| iPhone (tested: iPhone 17 Pro, iOS 18) | ✅ | ✅ | ✅ |
| Other iPhone / iOS | ✅ likely | ✅ likely | ✅ likely |
| Android | ⚠️ untested | ⚠️ untested | ⚠️ untested |

## Project Structure

```
bluetooth-media-bridge/
├── app/                  # Python GUI (PySide6)
│   ├── main.py           # Entry point
│   ├── bridge_engine.py  # Core orchestrator
│   ├── tray_app.py       # System tray
│   ├── settings_window.py
│   ├── ipc_client.py     # TCP client for bt_bridge
│   ├── smtc_manager.py   # Windows media controls
│   └── single_instance.py
├── bluetooth_bridge/
│   └── btstack/          # BTstack submodule
│       └── port/windows-winusb/
│           └── bt_bridge.c   # C core (A2DP sink + IPC server)
└── config.json           # Runtime config (auto-generated)
```

## Known Limitations

- Realtek dongles only (for now)
- Windows only
- Dongle must use WinUSB driver — incompatible with Windows native Bluetooth stack while active
- Running outside MSYS2 terminal requires `C:\msys64\mingw64\bin` in system PATH

---

## License

Based on [BTstack](https://github.com/bluekitchen/btstack) by BlueKitchen GmbH (non-commercial license).  
Python GUI and bt_bridge.c modifications: MIT.
