# AJ179 Battery Tray

English · [简体中文](README.md)

> **Using an AJ159 NearLink mouse?** It uses a different protocol (receiver `7768:6425`)
> and is not supported here. See
> [ajazz-aj159-nealink-battery](https://github.com/Teristans/ajazz-aj159-nealink-battery).

[![Platform](https://img.shields.io/badge/platform-Windows-0078d4)](https://github.com/)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Driver](https://img.shields.io/badge/vendor%20driver-not%20required-success)]()

Battery percentage in the Windows system tray for the **AJAZZ AJ179 APEX** wireless mouse,
plus **OLED clock sync for the charging dock**.
Talks to the HID device directly — no vendor driver, no network, no changes to your mouse settings.

![Tray icon states](assets/icon-states.png)

## Features

- Tray icon shows the **battery percentage**: green (>50%) / orange (21–50%) / red (≤20%), blue while charging
- Hover for percentage and last-updated time; left click to refresh (auto every 60 s)
- One Windows notification when battery drops to ≤20%
- **Dock OLED clock**: pushes the system time to the charging dock every 30 s while running
- Optional run-at-login (`HKCU\...\Run`, no shortcut files left behind)
- Single instance; `--once` prints JSON so you can wire it into your own scripts

## Quick start

Windows + Python 3.9 or newer ([download](https://www.python.org/downloads/windows/), tick *Add Python to PATH*).

```text
1. Double-click setup.bat    # creates .venv, installs deps (once, then trims it)
2. Double-click start.bat    # percentage appears in the tray
```

Or manually:

```bash
pip install -r requirements.txt
pythonw aj179_tray.py
```

> Windows may hide the icon — click `^` in the taskbar and drag it out to pin it.

## Usage

Right-click the tray icon:

| Menu | Action |
|---|---|
| Refresh now | Read once (same as left click) |
| Details | State, product name, timestamp, raw HID frame as hex |
| Dock clock | Toggle clock sync (persisted in `config.json`) |
| Sync clock now | Force one clock update |
| Run at login | Registry autostart toggle |
| Quit | Exit |

Re-toggle "Run at login" after moving the folder.

## CLI

```bash
python aj179_tray.py --once         # one-shot JSON (includes clock_synced)
python aj179_tray.py --sync-clock   # push the clock once
python aj179_tray.py --quit         # graceful exit of a running instance
python aj179_battery.py             # human-readable state + raw frame
```

## Protocol

Reverse-engineered by hand — there is no public documentation for this model. Useful if you own a similar SONiX-based AJAZZ device.

VID `0x3151`, vendor control collection `usage_page=0xFFFF, usage=0x02`.

| Form factor | PID | Product string |
|---|---|---|
| 2.4G receiver | `0x5007` | AJAZZ 2.4G 8K |
| Wired / charging | `0x4026` | AJAZZ AJ179 APEX |

### Battery: reading alone returns zeros

The receiver only brings up the 2.4G telemetry link after it receives the vendor status poll.
Without it the status page is **all zeros, always**. Every read is therefore three steps:

1. **SET_FEATURE** status poll — report id `0x00`, opcode `0xF7`, everything else zero, no payload, no checksum. This is the heartbeat the vendor driver sends once per second.
2. Wait ~50 ms.
3. **GET_FEATURE** report `0x05` — percentage in byte 3:

```text
05 00 00 38 01 00 01 02
      ^^^^^^
      0x38 = 56%
```

A frame is valid only if the bytes between the report id and the percentage are zero (garbage shows up right after reconnects).
An all-zero frame means the link is not ready — treated as *unknown*, **never reported as 0%**.

### Dock clock: `0x28 SET_OLEDCLOCK`

64-byte feature report on the same collection, **no checksum**:

```text
frame[0] = 0x00 report id     frame[1] = 0x28 command     frame[2..7] = 0
frame[8] = 0xD7 required marker
frame[9..10] = year (big endian)   frame[11] = month  frame[12] = day
frame[13] = hour  frame[14] = minute  frame[15] = second
```

Verified with `2026-11-10 18:30:00`:

```text
00 28 00 00 00 00 00 00 d7 07 ea 0b 0a 12 1e 00
```

Three non-obvious gotchas:

1. **Drop the `0xD7` marker and the firmware silently discards the packet** — no error, no ack. This is the most common reason clock sync "doesn't work".
2. **You must resend periodically.** The dock resets itself back to `00:00` within a few minutes, and the device never leaves the USB tree, so there is no hotplug event to hook. Sending every 30 s is how the feature is supposed to work, not a fallback.
3. The firmware only starts accepting the frame ~2 s after power-up; the app waits 3 s before the first send.

The command has **no acknowledgement**. A successful send only means the USB write went through — the only way to verify is to look at the dock's screen.

### Charging state

Plugging in changes the PID from `0x5007` to `0x4026` and the mouse switches to the wired channel:

- if a percentage is still readable → blue number
- otherwise → blue **充** glyph, never a made-up 0%

This part came from plug/unplug testing; the public reverse-engineering docs do not mention it.

## Compatibility

Matching is by VID `0x3151` plus the vendor interface signature. **Heads up: AJAZZ ships two unrelated wireless platforms — the "NearLink / 星闪" models are not covered.**

| Model | Wireless platform | Status |
|---|---|---|
| AJ179 APEX (2.4G) | SONiX `0x3151` | ✅ Confirmed working (dev device) |
| AJ159 APEX (2.4G) | SONiX `0x3151` | ⚠️ Same platform, untested |
| **AJ159 NL / NearLink** | NearLink BS20 `0x7768` | ❌ **Not supported** |

The NearLink variant is a different chipset and protocol: VID `0x7768` (`7768:6425` receiver, `7768:6D25` wired),
vendor collections on usage pages `0xFFB1 / 0xFFB2 / 0xFFB3`, report id `6`, and the **battery level is pushed by
the device** instead of being queried:

```text
06 fa 03 00 00 03 64 01
|  |  |        |  |
|  |  |        |  +-- charging flag (1 = on USB power)
|  |  |        +----- percentage 0x64 = 100%
|  |  +-------------- tag 0x03 = battery level
+--+----------------- 5-byte header
```

Supporting it needs a new adapter (input-report listener plus different discovery rules); this version does not have one.

**Not sure which one you have?** Run the read-only probe:

```bash
python tools/probe_hid.py          # human readable
python tools/probe_hid.py --json   # paste into an issue
```

It enumerates every HID device, the vendor collections, and any battery-looking field (percentage highlighted),
then tells you whether this project can drive it. Other brands (Logitech uses HID++) are not applicable at all.

## Tests

Frame parsing is pure functions — no hardware needed:

```bash
python -m unittest discover -s tests -v
```

## Footprint

A freshly installed `.venv` is about 28 MB, but only ~7 MB is ever imported:

| What | Size | Why it goes |
|---|---|---|
| `pip` | 13 MB | used once to install, never again |
| Pillow AVIF decoder | 7.5 MB | we draw a 32x32 flat-colour bitmap |
| Pillow WebP / LittleCMS | 0.7 MB | same, unused |
| hidapi + pystray + Pillow core | 6.5 MB | **the part that matters** |

`setup.bat` calls `tools/slim_venv.py` automatically and leaves the venv at ~7 MB.
For an environment that is already set up, run `slim.bat`; to see the savings first,
run `python tools/slim_venv.py --check`.

Everything removed is restorable:

```bash
.venv\Scripts\python.exe -m ensurepip                              # pip back
.venv\Scripts\python.exe -m pip install --force-reinstall pillow   # full Pillow back
```

> Running the app afterwards regenerates a few hundred KB of `__pycache__`.
> That is normal bytecode caching, not the bloat coming back.

## Limitations

- When the mouse is deeply asleep the receiver returns nothing; the tray shows `--` and keeps the last reading until the next refresh.
- While the mouse is plugged in and charging, the dock's HID interface disappears from the system, so clock sync pauses; it resumes automatically within 30 s after unplugging (and immediately when the state flips back to wireless).
- Windows only (tray, registry autostart and the `ctypes` message box are Win32).

## Credits

Protocol details come from these public reverse-engineering efforts:

- [Aiacos/ajazz-control-center](https://github.com/Aiacos/ajazz-control-center) — AJAZZ AJ-series protocol docs
- [reicheltp/ajazz-macos-mousetime-sync](https://github.com/reicheltp/ajazz-macos-mousetime-sync) — dock clock frame layout
- [xrfc/gpw-battery-tray](https://github.com/xrfc/gpw-battery-tray) — the project that inspired this one
- [alfaisaldev/AJ159-NL-P-web-based-driver-micetil](https://github.com/alfaisaldev/AJ159-NL-P-web-based-driver-micetil) — NearLink (AJ159 NL) protocol, source of the compatibility table above

## License

[MIT](LICENSE)
