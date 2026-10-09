# Tapp

A lightweight acoustic tap detection and action trigger tool for Windows laptops (tested on Lenovo IdeaPad).

Tapp detects deliberate mechanical taps on your laptop's palm-rest chassis area using the built-in microphone, while rejecting false positives from keyboard typing, trackpad clicks, and ambient room noise. Detected tap gestures (single, double, or triple tap) are instantly mapped to custom Windows actions like volume control, media playback, or screenshots with low latency.

---

## Quick Start (3 Steps)

### Step 1: One-Time Setup
Run the setup script to install dependencies, run self-tests, and build the native executable:
```cmd
setup.bat
```

### Step 2: Calibrate Your Laptop
Run the guided 5-step wizard to tune microphone sensitivity for your specific chassis and room acoustics:
```cmd
run.bat
```
- Step 1: Check microphone and live oscilloscope
- Step 2: Sample room background noise (10s)
- Step 3: Record ~30 deliberate palm-rest taps
- Step 4: Sample typing and trackpad clicks for rejection (30s)
- Step 5: Save calibration and click **[Build Standalone tapp.exe]** (with 1-click Desktop shortcut)

### Step 3: Daily Use (Lightweight Background Runner)
Run the lightweight native app anytime:
```cmd
tapp.bat
```
*(or double-click `tapp.exe` directly on your Desktop / taskbar)*

---

## Default Gestures and Actions

| Gesture | Default Action |
| :--- | :--- |
| **Single Tap** | Volume Up |
| **Double Tap** | Volume Down |
| **Triple Tap** | Mute / Unmute Volume |

*Actions and sensitivity thresholds can be customized in the app or via `data/actions_config.json`.*
