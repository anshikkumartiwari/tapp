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
- Step 2: Follow the app's navigation / instructions to caliberate the app for your device and environment.
- Step 3: Save calibration and click **[Build Standalone tapp.exe]**  <--- now you only need this file only (< 200 Kb)
### Step 3: Daily Use (Lightweight Background Runner)
Double-click `tapp.exe` directly. Pin to Task Bar

---

## Default Gestures and Actions

| Gesture | Default Action |
| :--- | :--- |
| **Single Tap** | Play/Pause |
| **Double Tap** | None |
| **Triple Tap** | None |

*Actions and sensitivity thresholds can be customized in the app or via `data/actions_config.json`.*
