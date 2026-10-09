"""Standalone, lightweight, minimal Tap Action Mapping application.

Runs continuously in the background, captures palm-rest taps on the Lenovo IdeaPad,
and dispatches native Windows multimedia actions (Volume Up, Volume Down, Mute, Media).
Clean white UI (#FFFFFF background, #000000 text), low resource usage.
"""

from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

import numpy as np

from tapp_app.actions import (
    AVAILABLE_ACTIONS,
    ACTIONS_CONFIG_FILE,
    ActionSettings,
    GestureActionManager,
    execute_action,
)
from tapp_app.audio_engine import AudioCaptureEngine, list_input_devices
from tapp_app.calibration import CALIBRATION_FILE, load_calibration
from tapp_app.detector import DetectedEvent, DetectorConfig, RealtimeHitDetector


# Minimalist styling constants
BG_COLOR = "#FFFFFF"
FG_COLOR = "#000000"
MUTED_FG = "#555555"
BORDER_COLOR = "#000000"
LIGHT_BORDER = "#D0D0D0"
HIGHLIGHT_BG = "#000000"
HIGHLIGHT_FG = "#FFFFFF"

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_HEADING = ("Segoe UI", 10, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_BIG = ("Segoe UI", 15, "bold")


class TappActionApp:
    """Dedicated background utility for hit-driven Windows actions."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Tapp — Tap Actions (Lenovo IdeaPad)")
        self.root.geometry("740x620")
        self.root.minsize(680, 540)
        self.root.configure(bg=BG_COLOR)

        # Core Engines
        self.audio_engine = AudioCaptureEngine()
        self.detector = RealtimeHitDetector()
        self.action_manager = GestureActionManager(on_action_fired=self._on_action_fired)
        self.action_manager.load_settings()

        # UI Thread Queue
        self.ui_queue: queue.Queue = queue.Queue()
        self.serial_counter = 0

        # Load Tuned Calibration
        self._load_calibration_and_device()

        # Build Minimal UI Layout
        self._build_header()
        self._build_status_banner()
        self._build_controls()
        self._build_action_mappings()
        self._build_history_log()
        self._build_status_bar()

        # Connect Audio Callback & Auto-Start
        self.audio_engine.add_consumer(self._on_audio_block)
        self._start_listening()

        # UI Poll Loop
        self.root.after(35, self._ui_poll_loop)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_calibration_and_device(self):
        loaded = load_calibration()
        if loaded is not None:
            cfg, data = loaded
            self.detector.update_config(cfg)
            dev_meta = data.get("device", {})
            self.saved_dev_index = dev_meta.get("device_index")
            self.saved_dev_name = dev_meta.get("name", "Default Microphone")
        else:
            self.saved_dev_index = None
            self.saved_dev_name = "Default Microphone"

    def _start_listening(self):
        ok = self.audio_engine.start(device_index=self.saved_dev_index)
        if not ok:
            messagebox.showerror(
                "Audio Error",
                f"Could not open microphone: {self.audio_engine.error_message}\n"
                "Please verify microphone permissions in Windows Settings.",
            )

    def _build_header(self):
        header = tk.Frame(self.root, bg=BG_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        header.pack(fill="x", padx=14, pady=(12, 6))

        top_row = tk.Frame(header, bg=BG_COLOR)
        top_row.pack(fill="x", padx=12, pady=6)

        tk.Label(top_row, text="TAPP TAP ACTIONS", font=FONT_TITLE, bg=BG_COLOR, fg=FG_COLOR).pack(side="left")
        tk.Label(
            top_row,
            text=f"Mic: {self.saved_dev_name} | Floor: {self.detector.config.amplitude_floor:.3f}",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        ).pack(side="right")

    def _build_status_banner(self):
        self.action_banner = tk.Label(
            self.root,
            text="[ LISTENING FOR PALM-REST TAPS ]",
            font=FONT_BIG,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            pady=12,
        )
        self.action_banner.pack(fill="x", padx=14, pady=(0, 8))

    def _build_controls(self):
        ctrl_frame = tk.Frame(self.root, bg=BG_COLOR)
        ctrl_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.action_enable_var = tk.BooleanVar(value=self.action_manager.settings.enabled)
        self.chk_enable = tk.Checkbutton(
            ctrl_frame,
            text=" Enable Tap Action Dispatching (Sends Windows volume / media keys)",
            variable=self.action_enable_var,
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            activebackground=BG_COLOR,
            activeforeground=FG_COLOR,
            command=self._on_settings_changed,
        )
        self.chk_enable.pack(side="left")

        self.btn_pause_toggle = tk.Button(
            ctrl_frame,
            text="⏸ Pause Listening",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._toggle_listening,
            padx=10,
            pady=2,
        )
        self.btn_pause_toggle.pack(side="right")

    def _build_action_mappings(self):
        card = tk.LabelFrame(
            self.root,
            text=" Gesture Mappings & Sensitivity ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=8,
        )
        card.pack(fill="x", padx=14, pady=(0, 8))

        # 1. Single Tap
        r1 = tk.Frame(card, bg=BG_COLOR)
        r1.pack(fill="x", pady=3)
        tk.Label(r1, text="Single Tap ➔", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, width=16, anchor="w").pack(side="left")
        self.combo_single = ttk.Combobox(r1, values=AVAILABLE_ACTIONS, font=FONT_BODY, state="readonly", width=26)
        self.combo_single.set(self.action_manager.settings.single_tap_action)
        self.combo_single.pack(side="left", padx=6)
        self.combo_single.bind("<<ComboboxSelected>>", lambda e: self._on_settings_changed())

        tk.Button(
            r1,
            text="🔊 Test Vol Up",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._test_action(self.combo_single.get()),
            padx=8,
            pady=1,
        ).pack(side="left", padx=6)

        # 2. Double Tap
        r2 = tk.Frame(card, bg=BG_COLOR)
        r2.pack(fill="x", pady=3)
        tk.Label(r2, text="Double Tap ➔", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, width=16, anchor="w").pack(side="left")
        self.combo_double = ttk.Combobox(r2, values=AVAILABLE_ACTIONS, font=FONT_BODY, state="readonly", width=26)
        self.combo_double.set(self.action_manager.settings.double_tap_action)
        self.combo_double.pack(side="left", padx=6)
        self.combo_double.bind("<<ComboboxSelected>>", lambda e: self._on_settings_changed())

        tk.Button(
            r2,
            text="🔉 Test Vol Down",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._test_action(self.combo_double.get()),
            padx=8,
            pady=1,
        ).pack(side="left", padx=6)

        # 3. Triple Tap
        r3 = tk.Frame(card, bg=BG_COLOR)
        r3.pack(fill="x", pady=3)
        tk.Label(r3, text="Triple Tap ➔", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, width=16, anchor="w").pack(side="left")
        self.combo_triple = ttk.Combobox(r3, values=AVAILABLE_ACTIONS, font=FONT_BODY, state="readonly", width=26)
        self.combo_triple.set(self.action_manager.settings.triple_tap_action)
        self.combo_triple.pack(side="left", padx=6)
        self.combo_triple.bind("<<ComboboxSelected>>", lambda e: self._on_settings_changed())

        tk.Button(
            r3,
            text="🔇 Test Mute",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._test_action(self.combo_triple.get()),
            padx=8,
            pady=1,
        ).pack(side="left", padx=6)

        # Timing config row
        r4 = tk.Frame(card, bg=BG_COLOR)
        r4.pack(fill="x", pady=(6, 2))

        tk.Label(r4, text="Double-Tap Speed:", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR).pack(side="left")
        self.spin_dt_speed = tk.Spinbox(r4, from_=0.20, to=0.70, increment=0.02, font=FONT_BODY, width=6, bd=1, relief="solid")
        self.spin_dt_speed.delete(0, tk.END)
        self.spin_dt_speed.insert(0, f"{self.action_manager.settings.double_tap_timeout_sec:.2f}")
        self.spin_dt_speed.pack(side="left", padx=(6, 8))
        self.spin_dt_speed.config(command=self._on_settings_changed)

        tk.Label(r4, text="sec window | Tuned Tap Threshold: Peak >= 0.185", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(side="left")

        tk.Button(
            r4,
            text="💾 Save Preferences",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._save_settings_manual,
            padx=10,
            pady=2,
        ).pack(side="right")

    def _build_history_log(self):
        log_card = tk.LabelFrame(
            self.root,
            text=" Action Execution History Log ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=6,
        )
        log_card.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        top_ctrl = tk.Frame(log_card, bg=BG_COLOR)
        top_ctrl.pack(fill="x", pady=(0, 4))

        tk.Label(top_ctrl, text="Real-time log of dispatched system commands:", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(side="left")

        tk.Button(
            top_ctrl,
            text="Clear Log",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self.log_list.delete(0, tk.END),
            padx=8,
            pady=1,
        ).pack(side="right")

        scroll = tk.Scrollbar(log_card)
        scroll.pack(side="right", fill="y")

        self.log_list = tk.Listbox(
            log_card,
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
            selectbackground=HIGHLIGHT_BG,
            selectforeground=HIGHLIGHT_FG,
            bd=1,
            relief="solid",
            yscrollcommand=scroll.set,
        )
        self.log_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.log_list.yview)

    def _build_status_bar(self):
        status_bar = tk.Frame(self.root, bg=BG_COLOR, bd=0, highlightthickness=1, highlightbackground=LIGHT_BORDER)
        status_bar.pack(fill="x", side="bottom", padx=14, pady=(2, 8))

        self.status_lbl = tk.Label(
            status_bar,
            text="Listening for taps on right palm-rest area near Lenovo logo...",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
            anchor="w",
        )
        self.status_lbl.pack(side="left", padx=8, pady=3)

    def _toggle_listening(self):
        if self.audio_engine.is_running:
            self.audio_engine.stop()
            self.btn_pause_toggle.config(text="▶ Resume Listening")
            self.action_banner.config(text="[ PAUSED — NOT LISTENING ]", bg=BG_COLOR, fg=MUTED_FG)
            self.status_lbl.config(text="Audio stream paused.")
        else:
            self._start_listening()
            self.btn_pause_toggle.config(text="⏸ Pause Listening")
            self.action_banner.config(text="[ LISTENING FOR PALM-REST TAPS ]", bg=BG_COLOR, fg=FG_COLOR)
            self.status_lbl.config(text="Listening for palm-rest taps...")

    def _on_audio_block(self, block: np.ndarray, current_sec: float):
        event = self.detector.process_block(block, current_sec)
        if event is not None:
            self.action_manager.on_hit_detected()

    def _on_action_fired(self, gesture: str, action: str):
        self.ui_queue.put((gesture, action))

    def _test_action(self, action_name: str):
        execute_action(action_name)
        now_str = time.strftime("%H:%M:%S")
        self.serial_counter += 1
        log_line = f"#{self.serial_counter:02d} [{now_str}] 🧪 TEST TRIGGER ➔ Executed: {action_name}"
        self.log_list.insert(tk.END, log_line)
        self.log_list.see(tk.END)
        self.status_lbl.config(text=f"Test executed: {action_name}")

    def _on_settings_changed(self):
        try:
            dt_to = float(self.spin_dt_speed.get())
        except (ValueError, TypeError):
            dt_to = 0.38

        self.action_manager.settings.enabled = self.action_enable_var.get()
        self.action_manager.settings.single_tap_action = self.combo_single.get()
        self.action_manager.settings.double_tap_action = self.combo_double.get()
        self.action_manager.settings.triple_tap_action = self.combo_triple.get()
        self.action_manager.settings.double_tap_timeout_sec = dt_to
        self.action_manager.save_settings()

    def _save_settings_manual(self):
        self._on_settings_changed()
        messagebox.showinfo(
            "Saved",
            "Action settings saved to data/actions_config.json:\n\n"
            f"• Single Tap: {self.action_manager.settings.single_tap_action}\n"
            f"• Double Tap: {self.action_manager.settings.double_tap_action}\n"
            f"• Triple Tap: {self.action_manager.settings.triple_tap_action}\n"
            f"• Speed Window: {self.action_manager.settings.double_tap_timeout_sec}s",
        )

    def _ui_poll_loop(self):
        while not self.ui_queue.empty():
            try:
                gesture, action = self.ui_queue.get_nowait()
                self.serial_counter += 1
                now_str = time.strftime("%H:%M:%S")
                log_line = f"#{self.serial_counter:02d} [{now_str}] ⚡ {gesture.upper()} ➔ Dispatched: {action}"
                self.log_list.insert(tk.END, log_line)
                self.log_list.see(tk.END)

                self.action_banner.config(
                    text=f"⚡ {gesture.upper()} ➔ {action}",
                    bg=HIGHLIGHT_BG,
                    fg=HIGHLIGHT_FG,
                )
                self.root.after(750, self._reset_action_banner)
                self.status_lbl.config(text=f"Executed: {gesture} ➔ {action}")
            except queue.Empty:
                break

        self.root.after(35, self._ui_poll_loop)

    def _reset_action_banner(self):
        if self.audio_engine.is_running:
            self.action_banner.config(text="[ LISTENING FOR PALM-REST TAPS ]", bg=BG_COLOR, fg=FG_COLOR)
        else:
            self.action_banner.config(text="[ PAUSED — NOT LISTENING ]", bg=BG_COLOR, fg=MUTED_FG)

    def _on_close(self):
        self.audio_engine.stop()
        self.root.destroy()


# Backward-compatibility alias
NokActionApp = TappActionApp


def main():
    root = tk.Tk()
    app = TappActionApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
