# Tapp

A lightweight acoustic tap detection and action trigger tool for Windows laptops (tested on Lenovo IdeaPad).

Tapp detects deliberate mechanical taps on your laptop's palm-rest chassis area using the built-in microphone, while rejecting false positives from keyboard typing, trackpad clicks, and ambient room noise. Detected tap gestures (single, double, or triple tap) are instantly mapped to custom Windows actions like volume control, media playback, or screenshots with low latency.

---

## How to Run

### 1. Setup

Run the setup script to install dependencies (`numpy`, `sounddevice`, `scipy`):

```cmd
setup.bat
```

*Or install manually via pip:*
```cmd
pip install -r requirements.txt
```

---

### 2. Launch

You can launch Tapp in any of three ways:

* **Full Test & Calibration Suite** (GUI for device selection, calibration, and live accuracy testing):
  ```cmd
  run.bat
  ```
  *(or `python main.py`)*

* **Dedicated Tap Action Companion** (Compact background utility for gesture execution):
  ```cmd
  run_actions.bat
  ```
  *(or `python action_app.py`)*

* **Native C# / .NET 8 Standalone App** (Zero-Python native build with minimal RAM & low latency):
  ```cmd
  run_native_actions.bat
  ```
  *(or `dotnet run --project TappActionApp\TappActionApp.csproj -c Release`)*

---

## Default Gestures & Actions

| Gesture | Default Action |
| :--- | :--- |
| **Single Tap** | Volume Up |
| **Double Tap** | Volume Down |
| **Triple Tap** | Mute / Unmute Volume |

*Actions and sensitivity thresholds can be customized directly in the app or via `data/actions_config.json`.*
