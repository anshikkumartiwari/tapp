"""System action dispatcher and gesture recognition (Single Tap, Double Tap, Triple Tap).

Dispatches native Windows multimedia and volume events using ctypes.user32.
Recognizes tap gestures using an inter-onset-interval (IOI) state machine.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import threading
import time
from typing import Callable, Dict, List, Optional
import ctypes


ACTIONS_CONFIG_FILE = Path("data") / "actions_config.json"

# Windows Virtual Key Codes
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_SNAPSHOT = 0x2C
KEYEVENTF_KEYUP = 0x0002


def send_windows_vk(vk_code: int):
    """Press and release a virtual key code using user32.keybd_event."""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.01)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


AVAILABLE_ACTIONS = [
    "Volume Up",
    "Volume Down",
    "Mute / Unmute Volume",
    "Play / Pause Media",
    "Next Track",
    "Previous Track",
    "Screenshot (PrintScreen)",
    "None (Detection Only)",
]


def execute_action(action_name: str) -> bool:
    """Execute the configured action on Windows."""
    if action_name == "Volume Up":
        send_windows_vk(VK_VOLUME_UP)
        return True
    elif action_name == "Volume Down":
        send_windows_vk(VK_VOLUME_DOWN)
        return True
    elif action_name == "Mute / Unmute Volume":
        send_windows_vk(VK_VOLUME_MUTE)
        return True
    elif action_name == "Play / Pause Media":
        send_windows_vk(VK_MEDIA_PLAY_PAUSE)
        return True
    elif action_name == "Next Track":
        send_windows_vk(VK_MEDIA_NEXT_TRACK)
        return True
    elif action_name == "Previous Track":
        send_windows_vk(VK_MEDIA_PREV_TRACK)
        return True
    elif action_name == "Screenshot (PrintScreen)":
        send_windows_vk(VK_SNAPSHOT)
        return True
    elif action_name == "None (Detection Only)":
        return True
    return False


@dataclass
class ActionSettings:
    enabled: bool = True
    single_tap_action: str = "Volume Up"
    double_tap_action: str = "Volume Down"
    triple_tap_action: str = "Mute / Unmute Volume"
    double_tap_timeout_sec: float = 0.38  # max interval to wait for a 2nd tap

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "ActionSettings":
        valid = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in d.items() if k in valid}
        return cls(**filtered)


class GestureActionManager:
    """Tracks detected tap timestamps, resolves single/double/triple taps, and executes actions."""

    def __init__(self, settings: Optional[ActionSettings] = None, on_action_fired: Optional[Callable[[str, str], None]] = None):
        self.settings = settings or ActionSettings()
        self.on_action_fired = on_action_fired  # callback(gesture_name, action_name)
        self._tap_chain: List[float] = []
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def load_settings(self, filepath: Path = ACTIONS_CONFIG_FILE):
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    self.settings = ActionSettings.from_dict(json.load(f))
            except Exception:
                pass

    def save_settings(self, filepath: Path = ACTIONS_CONFIG_FILE):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.settings.to_dict(), f, indent=2)
        except Exception:
            pass

    def on_hit_detected(self):
        """Called whenever a hit onset is detected by RealtimeHitDetector."""
        if not self.settings.enabled:
            return

        now = time.time()
        with self._lock:
            # Cancel pending single-tap timer
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            # Clean chain if last tap was too long ago
            timeout = self.settings.double_tap_timeout_sec
            if self._tap_chain and (now - self._tap_chain[-1]) > timeout:
                self._tap_chain = []

            self._tap_chain.append(now)
            chain_len = len(self._tap_chain)

            if chain_len == 1:
                # First tap: wait timeout before deciding single tap
                self._timer = threading.Timer(timeout, self._finalize_single_tap)
                self._timer.daemon = True
                self._timer.start()
            elif chain_len == 2:
                # Second tap: wait small window in case it's a triple tap
                self._timer = threading.Timer(timeout, self._finalize_double_tap)
                self._timer.daemon = True
                self._timer.start()
            elif chain_len >= 3:
                # Third tap: fire immediately!
                self._tap_chain = []
                self._dispatch_action("Triple Tap", self.settings.triple_tap_action)

    def _finalize_single_tap(self):
        with self._lock:
            if len(self._tap_chain) == 1:
                self._tap_chain = []
                self._dispatch_action("Single Tap", self.settings.single_tap_action)

    def _finalize_double_tap(self):
        with self._lock:
            if len(self._tap_chain) == 2:
                self._tap_chain = []
                self._dispatch_action("Double Tap", self.settings.double_tap_action)

    def _dispatch_action(self, gesture: str, action: str):
        if not self.settings.enabled or action == "None (Detection Only)":
            return

        execute_action(action)
        if self.on_action_fired:
            self.on_action_fired(gesture, action)

    # Backward-compatibility alias
    on_knock_detected = on_hit_detected

