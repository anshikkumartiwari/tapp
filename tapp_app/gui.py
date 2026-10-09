"""Tkinter graphical user interface for Tapp Hit Detection.

Clean, minimal design with default white background and black text.
Features a beginner-friendly 5-step guided calibration wizard, standalone
tapp.exe compilation with hardcoded calibration, 1-click desktop shortcut creation,
and an optional advanced metrics lab.
"""

from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple
import numpy as np

from .actions import AVAILABLE_ACTIONS, ActionSettings, GestureActionManager, execute_action
from .audio_engine import AudioCaptureEngine, AudioDeviceInfo, list_input_devices
from .builder import build_standalone_executable, create_desktop_shortcut
from .calibration import (
    CALIBRATION_FILE,
    LABELS_CSV_FILE,
    LIVE_GROUND_TRUTH_FILE,
    export_labels_to_csv,
    load_calibration,
    load_labels_from_csv,
    load_live_ground_truth_events,
    optimize_calibration,
    parse_log_text_lines,
    recalibrate_with_live_ground_truth,
    save_calibration,
    save_live_ground_truth_event,
    update_live_ground_truth_file,
)
from .detector import DetectedEvent, DetectorConfig, RealtimeHitDetector, RealtimeKnockDetector
from .metrics import EvaluationMetrics, generate_markdown_report
from .segmenter import CandidateEvent, save_candidate_wav, segment_audio_recording

# Styling constants: Minimal white background, black text
BG_COLOR = "#FFFFFF"
FG_COLOR = "#000000"
MUTED_FG = "#666666"
BORDER_COLOR = "#000000"
LIGHT_BORDER = "#DCDCDC"
ACTIVE_BG = "#EAEAEA"
HIGHLIGHT_BG = "#000000"
HIGHLIGHT_FG = "#FFFFFF"

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_HEADING = ("Segoe UI", 11, "bold")
FONT_MEDIUM = ("Segoe UI", 10)
FONT_BODY = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_BIG = ("Segoe UI", 15, "bold")


class TappAppGUI:
    """Main Application GUI with guided calibration wizard."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Tapp - Setup and Calibration Wizard")
        self.root.geometry("980x720")
        self.root.minsize(880, 640)
        self.root.configure(bg=BG_COLOR)

        # Core Audio & Detector Engines
        self.audio_engine = AudioCaptureEngine()
        self.detector = RealtimeHitDetector()
        self.audio_engine.add_consumer(self._on_audio_block)

        # Calibration State
        self.ambient_audio: Optional[np.ndarray] = None
        self.tap_candidates: List[CandidateEvent] = []
        self.negative_candidates: List[CandidateEvent] = []
        self.calibrated_config: Optional[DetectorConfig] = None
        self.last_calibration_summary: Optional[Dict] = None

        # Wizard Navigation State (0: Device, 1: Silence, 2: Taps, 3: Typing, 4: Build)
        self.current_step = 0
        self.is_recording_phase = False
        self.phase_cancel_requested = False
        self.recording_stop_event = threading.Event()
        self.selected_device_name = "Default Microphone"
        self.selected_device_index: Optional[int] = None

        # Live Evaluation / Advanced Lab State
        self.is_lab_mode = False
        self.live_events: List[DetectedEvent] = []
        self.live_log_entries: List[Dict] = []
        self.evaluation_metrics = EvaluationMetrics()
        self.live_session_start_time = 0.0
        self.negative_session_start_time = 0.0
        self.is_live_detecting = False
        self.is_alternating_test = False
        self.alternating_step = 0

        # Gesture Action Manager
        self.action_manager = GestureActionManager(on_action_fired=self._on_action_fired)
        self.action_manager.load_settings()

        # UI Thread Queue
        self.ui_update_queue: queue.Queue = queue.Queue()

        # Build UI layout
        self._build_top_header()
        self._build_main_container()
        self._build_status_bar()

        # Initialize devices and existing calibration
        self._refresh_devices()
        self._load_saved_calibration_if_exists()
        self._load_saved_live_ground_truth_if_exists()

        # Start periodic UI polling loop
        self.root.after(40, self._ui_poll_loop)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------------------------------
    # Layout Builders
    # -------------------------------------------------------------------------

    def _build_top_header(self):
        header_frame = tk.Frame(self.root, bg=BG_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        header_frame.pack(fill="x", padx=14, pady=(12, 6))

        top_bar = tk.Frame(header_frame, bg=BG_COLOR)
        top_bar.pack(fill="x", padx=12, pady=8)

        left_box = tk.Frame(top_bar, bg=BG_COLOR)
        left_box.pack(side="left")

        title_lbl = tk.Label(
            left_box,
            text="TAPP - PALM-REST TAP DETECTION SETUP",
            font=FONT_TITLE,
            bg=BG_COLOR,
            fg=FG_COLOR,
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = tk.Label(
            left_box,
            text="Calibrate sensitivity for your laptop microphone and build standalone tapp.exe",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        )
        subtitle_lbl.pack(anchor="w")

        # Lab toggle button on the right
        self.btn_toggle_lab = tk.Button(
            top_bar,
            text="[ Open Advanced Lab ]",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            relief="groove",
            command=self._toggle_lab_mode,
        )
        self.btn_toggle_lab.pack(side="right", padx=4)

    def _build_main_container(self):
        # Outer host panel
        self.content_container = tk.Frame(self.root, bg=BG_COLOR)
        self.content_container.pack(fill="both", expand=True, padx=14, pady=6)

        # Wizard Screen Frame
        self.wizard_frame = tk.Frame(self.content_container, bg=BG_COLOR)
        self.wizard_frame.pack(fill="both", expand=True)

        # Lab Screen Frame (initially hidden)
        self.lab_frame = tk.Frame(self.content_container, bg=BG_COLOR)

        self._build_wizard_steps()
        self._build_advanced_lab()

    def _build_wizard_steps(self):
        # Step progress bar row
        self.step_nav_frame = tk.Frame(self.wizard_frame, bg=BG_COLOR)
        self.step_nav_frame.pack(fill="x", pady=(0, 10))

        self.step_buttons = []
        step_labels = [
            "1. Device Check",
            "2. Room Noise",
            "3. Palm Taps",
            "4. Typing Rejection",
            "5. Build tapp.exe",
        ]

        for i, text in enumerate(step_labels):
            btn = tk.Button(
                self.step_nav_frame,
                text=text,
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                bd=1,
                relief="solid",
                padx=8,
                pady=4,
                command=lambda idx=i: self._jump_to_step(idx),
            )
            btn.pack(side="left", padx=2, fill="x", expand=True)
            self.step_buttons.append(btn)

        # Body area where each step's content is rendered
        self.step_body_frame = tk.Frame(self.wizard_frame, bg=BG_COLOR, bd=1, relief="solid")
        self.step_body_frame.pack(fill="both", expand=True, pady=(0, 8))

        # Show initial Step 0
        self._show_step(0)

    # -------------------------------------------------------------------------
    # Wizard Navigation
    # -------------------------------------------------------------------------

    def _show_step(self, step_idx: int):
        self.current_step = step_idx

        # Update step nav button highlights
        for i, btn in enumerate(self.step_buttons):
            if i == step_idx:
                btn.config(bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
            else:
                btn.config(bg=BG_COLOR, fg=FG_COLOR)

        # Clear body frame
        for child in self.step_body_frame.winfo_children():
            child.destroy()

        if step_idx == 0:
            self._render_step_0_device()
        elif step_idx == 1:
            self._render_step_1_ambient()
        elif step_idx == 2:
            self._render_step_2_taps()
        elif step_idx == 3:
            self._render_step_3_typing()
        elif step_idx == 4:
            self._render_step_4_build()

    def _jump_to_step(self, step_idx: int):
        if self.is_recording_phase:
            self.recording_stop_event.set()
            self.is_recording_phase = False
        self._show_step(step_idx)

    # -------------------------------------------------------------------------
    # Step 0: Device Selection & Monitor
    # -------------------------------------------------------------------------

    def _render_step_0_device(self):
        pad = tk.Frame(self.step_body_frame, bg=BG_COLOR, padx=16, pady=16)
        pad.pack(fill="both", expand=True)

        # Instructions
        card = tk.LabelFrame(pad, text=" Welcome & Microphone Selection ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=12)
        card.pack(fill="x", pady=(0, 14))

        tk.Label(
            card,
            text=(
                "Welcome to Tapp. This wizard calibrates your laptop microphone to recognize chassis taps\n"
                "near the Lenovo logo while rejecting keyboard typing, trackpad clicks, and background noise.\n\n"
                "Verify that your microphone is active below. Tapping near the logo should create visible spikes."
            ),
            font=FONT_MEDIUM,
            bg=BG_COLOR,
            fg=FG_COLOR,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # Device Selection Controls
        dev_row = tk.Frame(card, bg=BG_COLOR)
        dev_row.pack(fill="x", pady=4)

        tk.Label(dev_row, text="Microphone:", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR).pack(side="left", padx=(0, 8))
        self.device_combo = ttk.Combobox(dev_row, state="readonly", width=48)
        self.device_combo.pack(side="left", padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        btn_refresh = tk.Button(dev_row, text="Refresh Devices", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=self._refresh_devices)
        btn_refresh.pack(side="left", padx=4)

        self.btn_audio_toggle = tk.Button(dev_row, text="Pause Audio", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=self._toggle_audio_stream)
        self.btn_audio_toggle.pack(side="left", padx=4)

        # Import Calibration profile row
        cfg_row = tk.Frame(card, bg=BG_COLOR)
        cfg_row.pack(fill="x", pady=(6, 2))

        btn_import_cfg = tk.Button(
            cfg_row,
            text="Import calibration.json",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            command=self._import_calibration_dialog,
            padx=10,
            pady=3,
        )
        btn_import_cfg.pack(side="left", padx=(0, 8))

        if CALIBRATION_FILE.exists():
            btn_load_def = tk.Button(
                cfg_row,
                text="Load Default Profile (data/calibration.json)",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                command=self._load_saved_calibration_and_notify,
                padx=10,
                pady=3,
            )
            btn_load_def.pack(side="left")

        # Live VU Meter & Waveform
        meter_card = tk.LabelFrame(pad, text=" Live Input Level & Oscilloscope ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=10)
        meter_card.pack(fill="both", expand=True, pady=(0, 14))

        # Level VU Bar
        vu_row = tk.Frame(meter_card, bg=BG_COLOR)
        vu_row.pack(fill="x", pady=(0, 8))

        tk.Label(vu_row, text="Input Level:", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR).pack(side="left", padx=(0, 8))
        self.vu_canvas = tk.Canvas(vu_row, height=18, bg=BG_COLOR, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self.vu_canvas.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.vu_label = tk.Label(vu_row, text="Peak: 0.000", font=FONT_MONO, bg=BG_COLOR, fg=FG_COLOR, width=14)
        self.vu_label.pack(side="right")

        # Rolling Dual-Band Waveform
        self.wave_canvas = tk.Canvas(meter_card, bg=BG_COLOR, height=125, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self.wave_canvas.pack(fill="both", expand=True, pady=(0, 4))

        self.wave_freq_lbl = tk.Label(
            meter_card,
            text="Peak: 0.000 | Dominant Frequency: Idle | Centroid: 0 Hz | Ambient Room Sound",
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
        )
        self.wave_freq_lbl.pack(anchor="w", pady=(2, 0))

        self.step0_status_lbl = tk.Label(
            meter_card,
            text="Microphone listening... Tap the palm rest or speak to test responsiveness.",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        )
        self.step0_status_lbl.pack(anchor="w", pady=(2, 0))

        # Bottom Bar: Start Calibration
        bottom_bar = tk.Frame(pad, bg=BG_COLOR)
        bottom_bar.pack(fill="x", pady=6)

        btn_start = tk.Button(
            bottom_bar,
            text="Start Calibration Wizard ->",
            font=FONT_BIG,
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
            padx=16,
            pady=8,
            command=lambda: self._show_step(1),
        )
        btn_start.pack(side="right")

    # -------------------------------------------------------------------------
    # Step 1: Ambient Room Noise (10s)
    # -------------------------------------------------------------------------

    def _render_step_1_ambient(self):
        pad = tk.Frame(self.step_body_frame, bg=BG_COLOR, padx=16, pady=16)
        pad.pack(fill="both", expand=True)

        card = tk.LabelFrame(pad, text=" Phase 1: Background Room Noise ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=14)
        card.pack(fill="x", pady=(0, 14))

        self.step1_instruct_lbl = tk.Label(
            card,
            text=(
                "Instruction:\n"
                "Please stay quiet and avoid touching your laptop for 10 seconds.\n"
                "We will measure continuous background room ambience and laptop fan noise."
            ),
            font=FONT_MEDIUM,
            bg=BG_COLOR,
            fg=FG_COLOR,
            justify="left",
        )
        self.step1_instruct_lbl.pack(anchor="w", pady=(0, 10))

        # Progress bar & countdown
        self.step1_progress = ttk.Progressbar(card, orient="horizontal", mode="determinate", length=400)
        self.step1_progress.pack(fill="x", pady=(0, 6))

        self.step1_status_lbl = tk.Label(card, text="Status: Ready to record.", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG)
        self.step1_status_lbl.pack(anchor="w")

        # Visualizer box
        vis_box = tk.LabelFrame(pad, text=" Live Dual-Band Waveforms ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=10, pady=8)
        vis_box.pack(fill="both", expand=True, pady=(0, 14))

        self.cal_canvas = tk.Canvas(vis_box, bg=BG_COLOR, height=110, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self.cal_canvas.pack(fill="both", expand=True)
        self.cal_freq_lbl = tk.Label(vis_box, text="Peak: 0.000 | Frequency: 0 Hz", font=FONT_MONO, bg=BG_COLOR, fg=FG_COLOR)
        self.cal_freq_lbl.pack(anchor="w", pady=(4, 0))

        # Bottom navigation row
        self.step1_nav = tk.Frame(pad, bg=BG_COLOR)
        self.step1_nav.pack(fill="x", pady=6)

        btn_back = tk.Button(
            self.step1_nav,
            text="<- Back to Step 1",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=10,
            pady=6,
            command=lambda: self._show_step(0),
        )
        btn_back.pack(side="left")

        self.step1_right_nav = tk.Frame(self.step1_nav, bg=BG_COLOR)
        self.step1_right_nav.pack(side="right")

        self.btn_step1_action = tk.Button(
            self.step1_right_nav,
            text="Record Room Noise (10s)",
            font=FONT_BIG,
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
            padx=16,
            pady=6,
            command=self._start_record_phase_a,
        )
        self.btn_step1_action.pack(side="right", padx=4)

        btn_proceed_step1 = tk.Button(
            self.step1_right_nav,
            text="Proceed to Step 3 ->",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=10,
            pady=6,
            command=lambda: self._show_step(2),
        )
        btn_proceed_step1.pack(side="right", padx=4)

        if self.ambient_audio is not None:
            self._display_step1_completed()

    def _display_step1_completed(self):
        floor = float(np.percentile(np.abs(self.ambient_audio), 99)) if self.ambient_audio is not None else 0.005
        self.step1_status_lbl.config(
            text=f"Phase 1 Complete: Recorded ambient noise baseline (floor: {floor:.4f}).",
            fg=FG_COLOR,
        )
        if hasattr(self, "step1_right_nav"):
            for child in self.step1_right_nav.winfo_children():
                child.destroy()

            btn_next = tk.Button(
                self.step1_right_nav,
                text="Proceed to Step 3 ->",
                font=FONT_BIG,
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
                padx=14,
                pady=6,
                command=lambda: self._show_step(2),
            )
            btn_next.pack(side="right", padx=4)

            btn_redo = tk.Button(
                self.step1_right_nav,
                text="Redo Step 2",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                padx=12,
                pady=6,
                command=self._start_record_phase_a,
            )
            btn_redo.pack(side="right", padx=4)

    # -------------------------------------------------------------------------
    # Step 2: Positive Palm Taps (30 taps)
    # -------------------------------------------------------------------------

    def _render_step_2_taps(self):
        pad = tk.Frame(self.step_body_frame, bg=BG_COLOR, padx=16, pady=16)
        pad.pack(fill="both", expand=True)

        card = tk.LabelFrame(pad, text=" Phase 2: Deliberate Palm-Rest Taps ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=14)
        card.pack(fill="x", pady=(0, 14))

        self.step2_instruct_lbl = tk.Label(
            card,
            text=(
                "Instruction:\n"
                "Deliberately tap the right-side palm rest near the Lenovo logo approximately 30 times.\n"
                "Leave a brief pause (~1 second) between each tap."
            ),
            font=FONT_MEDIUM,
            bg=BG_COLOR,
            fg=FG_COLOR,
            justify="left",
        )
        self.step2_instruct_lbl.pack(anchor="w", pady=(0, 10))

        self.step2_progress = ttk.Progressbar(card, orient="horizontal", mode="determinate", length=400)
        self.step2_progress.pack(fill="x", pady=(0, 6))

        self.step2_status_lbl = tk.Label(card, text="Status: Ready to record taps.", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG)
        self.step2_status_lbl.pack(anchor="w")

        # Visualizer box
        vis_box = tk.LabelFrame(pad, text=" Live Dual-Band Waveforms ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=10, pady=8)
        vis_box.pack(fill="both", expand=True, pady=(0, 14))

        self.cal_canvas = tk.Canvas(vis_box, bg=BG_COLOR, height=110, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self.cal_canvas.pack(fill="both", expand=True)
        self.cal_freq_lbl = tk.Label(vis_box, text="Peak: 0.000 | Frequency: 0 Hz", font=FONT_MONO, bg=BG_COLOR, fg=FG_COLOR)
        self.cal_freq_lbl.pack(anchor="w", pady=(4, 0))

        # Bottom navigation
        self.step2_nav = tk.Frame(pad, bg=BG_COLOR)
        self.step2_nav.pack(fill="x", pady=6)

        btn_back = tk.Button(
            self.step2_nav,
            text="<- Back to Step 2",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=10,
            pady=6,
            command=lambda: self._show_step(1),
        )
        btn_back.pack(side="left")

        self.step2_right_nav = tk.Frame(self.step2_nav, bg=BG_COLOR)
        self.step2_right_nav.pack(side="right")

        self.btn_step2_action = tk.Button(
            self.step2_right_nav,
            text="Start Recording Taps (30s)",
            font=FONT_BIG,
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
            padx=16,
            pady=6,
            command=self._start_record_phase_b,
        )
        self.btn_step2_action.pack(side="right", padx=4)

        btn_proceed_step2 = tk.Button(
            self.step2_right_nav,
            text="Proceed to Step 4 ->",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=10,
            pady=6,
            command=lambda: self._show_step(3),
        )
        btn_proceed_step2.pack(side="right", padx=4)

        if len(self.tap_candidates) > 0:
            self._display_step2_completed()

    def _display_step2_completed(self):
        count = len(self.tap_candidates)
        if count >= 10:
            msg = f"Phase 2 Complete: Successfully captured {count} valid chassis taps."
        elif count > 0:
            msg = f"Phase 2 Complete: Detected {count} taps (15-30 recommended). You can Proceed or Redo."
        else:
            msg = "Phase 2 Complete: No taps detected above baseline. You can Redo or Proceed."
        self.step2_status_lbl.config(text=msg, fg=FG_COLOR)

        if hasattr(self, "step2_right_nav"):
            for child in self.step2_right_nav.winfo_children():
                child.destroy()

            btn_next = tk.Button(
                self.step2_right_nav,
                text="Proceed to Step 4 ->",
                font=FONT_BIG,
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
                padx=14,
                pady=6,
                command=lambda: self._show_step(3),
            )
            btn_next.pack(side="right", padx=4)

            btn_redo = tk.Button(
                self.step2_right_nav,
                text="Redo Step 3 (Palm Taps)",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                padx=12,
                pady=6,
                command=self._start_record_phase_b,
            )
            btn_redo.pack(side="right", padx=4)

    # -------------------------------------------------------------------------
    # Step 3: Typing & Clicks (Negative Samples, 30s)
    # -------------------------------------------------------------------------

    def _render_step_3_typing(self):
        pad = tk.Frame(self.step_body_frame, bg=BG_COLOR, padx=16, pady=16)
        pad.pack(fill="both", expand=True)

        card = tk.LabelFrame(pad, text=" Phase 3: Keyboard Typing & Trackpad Clicks ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=14)
        card.pack(fill="x", pady=(0, 14))

        self.step3_instruct_lbl = tk.Label(
            card,
            text=(
                "Instruction:\n"
                "Type normally on your keyboard and click the trackpad for 30 seconds.\n"
                "We use this to train false-alarm rejection so typing never accidentally triggers actions."
            ),
            font=FONT_MEDIUM,
            bg=BG_COLOR,
            fg=FG_COLOR,
            justify="left",
        )
        self.step3_instruct_lbl.pack(anchor="w", pady=(0, 10))

        self.step3_progress = ttk.Progressbar(card, orient="horizontal", mode="determinate", length=400)
        self.step3_progress.pack(fill="x", pady=(0, 6))

        self.step3_status_lbl = tk.Label(card, text="Status: Ready to record typing activity.", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG)
        self.step3_status_lbl.pack(anchor="w")

        # Visualizer box
        vis_box = tk.LabelFrame(pad, text=" Live Dual-Band Waveforms ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=10, pady=8)
        vis_box.pack(fill="both", expand=True, pady=(0, 14))

        self.cal_canvas = tk.Canvas(vis_box, bg=BG_COLOR, height=110, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self.cal_canvas.pack(fill="both", expand=True)
        self.cal_freq_lbl = tk.Label(vis_box, text="Peak: 0.000 | Frequency: 0 Hz", font=FONT_MONO, bg=BG_COLOR, fg=FG_COLOR)
        self.cal_freq_lbl.pack(anchor="w", pady=(4, 0))

        # Bottom navigation
        self.step3_nav = tk.Frame(pad, bg=BG_COLOR)
        self.step3_nav.pack(fill="x", pady=6)

        btn_back = tk.Button(
            self.step3_nav,
            text="<- Back to Step 3",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=10,
            pady=6,
            command=lambda: self._show_step(2),
        )
        btn_back.pack(side="left")

        self.step3_right_nav = tk.Frame(self.step3_nav, bg=BG_COLOR)
        self.step3_right_nav.pack(side="right")

        self.btn_step3_action = tk.Button(
            self.step3_right_nav,
            text="Record Typing Activity (30s)",
            font=FONT_BIG,
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
            padx=16,
            pady=6,
            command=self._start_record_phase_c,
        )
        self.btn_step3_action.pack(side="right", padx=4)

        btn_proceed_step3 = tk.Button(
            self.step3_right_nav,
            text="Fit Model & Proceed ->",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=10,
            pady=6,
            command=self._execute_calibration_fitting,
        )
        btn_proceed_step3.pack(side="right", padx=4)

        if len(self.negative_candidates) > 0:
            self._display_step3_completed()

    def _display_step3_completed(self):
        count = len(self.negative_candidates)
        self.step3_status_lbl.config(
            text=f"Phase 3 Complete: Captured typing audio ({count} candidate transients).",
            fg=FG_COLOR,
        )
        if hasattr(self, "step3_right_nav"):
            for child in self.step3_right_nav.winfo_children():
                child.destroy()

            btn_finish = tk.Button(
                self.step3_right_nav,
                text="Fit Model & Proceed to Step 5 ->",
                font=FONT_BIG,
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
                padx=14,
                pady=6,
                command=self._execute_calibration_fitting,
            )
            btn_finish.pack(side="right", padx=4)

            btn_redo = tk.Button(
                self.step3_right_nav,
                text="Redo Step 4",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                padx=12,
                pady=6,
                command=self._start_record_phase_c,
            )
            btn_redo.pack(side="right", padx=4)

    # -------------------------------------------------------------------------
    # Step 4: Fit Model & Standalone Builder
    # -------------------------------------------------------------------------

    def _render_step_4_build(self):
        pad = tk.Frame(self.step_body_frame, bg=BG_COLOR, padx=16, pady=16)
        pad.pack(fill="both", expand=True)

        card = tk.LabelFrame(pad, text=" Calibration Summary & Standalone Builder ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=14)
        card.pack(fill="x", pady=(0, 14))

        cfg = self.calibrated_config or self.detector.config

        summary_text = (
            f"Tuned Acoustic Profile:\n"
            f"- Amplitude Floor: {cfg.amplitude_floor:.3f}\n"
            f"- Attack Transient Ratio: {cfg.attack_ratio_threshold:.1f}x\n"
            f"- Maximum Chassis Centroid: {cfg.centroid_max_hz:.0f} Hz\n"
            f"- High/Low Energy Ratio Limit: {cfg.high_low_ratio_max:.2f}\n"
            f"- Minimum Confidence Score: {cfg.min_confidence:.0%}\n\n"
            f"Your calibration is saved in data/calibration.json."
        )

        tk.Label(card, text=summary_text, font=FONT_MEDIUM, bg=BG_COLOR, fg=FG_COLOR, justify="left").pack(anchor="w", pady=(0, 12))

        # Build Box
        build_box = tk.LabelFrame(pad, text=" Build Standalone tapp.exe ", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, padx=14, pady=12)
        build_box.pack(fill="both", expand=True, pady=(0, 14))

        tk.Label(
            build_box,
            text=(
                "Click the button below to bake your calibrated profile directly into tapp.exe.\n"
                "The resulting standalone executable needs no Python runtime, consumes minimal RAM,\n"
                "and can be launched from your Desktop or System Tray."
            ),
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        self.build_status_lbl = tk.Label(build_box, text="Status: Ready to build.", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG)
        self.build_status_lbl.pack(anchor="w", pady=(0, 10))

        btn_row = tk.Frame(build_box, bg=BG_COLOR)
        btn_row.pack(fill="x", pady=4)

        self.btn_build_exe = tk.Button(
            btn_row,
            text="Build Standalone tapp.exe",
            font=FONT_BIG,
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
            padx=16,
            pady=8,
            command=self._build_standalone_app,
        )
        self.btn_build_exe.pack(side="left", padx=(0, 8))

        self.btn_export_cfg = tk.Button(
            btn_row,
            text="Export calibration.json",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=12,
            pady=8,
            command=self._export_calibration_dialog,
        )
        self.btn_export_cfg.pack(side="left", padx=4)

        self.btn_shortcut = tk.Button(
            btn_row,
            text="Create Desktop Shortcut",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=12,
            pady=8,
            command=self._create_desktop_shortcut_manual,
        )
        self.btn_shortcut.pack(side="left", padx=4)

        self.btn_launch_native = tk.Button(
            btn_row,
            text="Launch tapp.exe",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            padx=12,
            pady=8,
            command=self._launch_standalone_app,
        )
        self.btn_launch_native.pack(side="left", padx=4)

        # Bottom row
        bottom_bar = tk.Frame(pad, bg=BG_COLOR)
        bottom_bar.pack(fill="x", pady=6)

        btn_back = tk.Button(bottom_bar, text="<- Back to Step 4", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=lambda: self._show_step(3))
        btn_back.pack(side="left")

    def _execute_calibration_fitting(self):
        """Fit calibration model and transition to Step 4."""
        taps = [c for c in self.tap_candidates if c.label == "tap"]
        negs = [c for c in self.negative_candidates if c.label != "tap"]
        if not taps:
            taps = self.tap_candidates

        cfg, summary = optimize_calibration(self.ambient_audio, taps, negs, sr=self.audio_engine.actual_sample_rate)
        self.calibrated_config = cfg
        self.last_calibration_summary = summary
        self.detector.config = cfg

        dev_meta = {
            "device_name": getattr(self, "selected_device_name", "Microphone Array"),
            "sample_rate": self.audio_engine.actual_sample_rate,
            "host_api": "Windows Sound Architecture",
        }
        save_calibration(cfg, summary, dev_meta)
        export_labels_to_csv(taps + negs)

        self._show_step(4)

    def _build_standalone_app(self):
        """Compile tapp.exe in background thread with hardcoded calibration."""
        self.btn_build_exe.config(state="disabled")
        self.build_status_lbl.config(text="Building standalone tapp.exe with baked calibration... (Please wait ~2-3s)", fg=FG_COLOR)

        def worker():
            cfg = self.calibrated_config or self.detector.config
            ok, msg = build_standalone_executable(cfg)
            self.root.after(0, lambda: self._on_build_completed(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_build_completed(self, success: bool, message: str):
        self.btn_build_exe.config(state="normal")
        if success:
            self.build_status_lbl.config(text=f"Build Successful: Created {message}", fg=FG_COLOR)
            # Prompt user to create desktop shortcut
            create_sc = messagebox.askyesno(
                "Tapp - Build Complete",
                "Standalone tapp.exe was built successfully with your calibration baked in!\n\n"
                "Would you like to create a shortcut on your Desktop now?",
                parent=self.root,
            )
            if create_sc:
                sc_ok, sc_msg = create_desktop_shortcut(message)
                if sc_ok:
                    messagebox.showinfo("Shortcut Created", f"Desktop shortcut created successfully:\n{sc_msg}", parent=self.root)
                else:
                    messagebox.showwarning("Shortcut Warning", f"Could not create shortcut: {sc_msg}", parent=self.root)
        else:
            self.build_status_lbl.config(text=f"Build failed: {message}", fg=FG_COLOR)
            messagebox.showerror("Build Error", f"Failed to compile standalone tapp.exe:\n\n{message}", parent=self.root)

    def _create_desktop_shortcut_manual(self):
        target = Path("tapp.exe")
        if not target.exists():
            target = Path("TappActionApp") / "publish" / "tapp.exe"
        if not target.exists():
            messagebox.showwarning("Not Found", "Please click 'Build Standalone tapp.exe' first before creating a shortcut.", parent=self.root)
            return

        ok, msg = create_desktop_shortcut(str(target))
        if ok:
            messagebox.showinfo("Shortcut Created", f"Desktop shortcut created successfully:\n{msg}", parent=self.root)
        else:
            messagebox.showwarning("Shortcut Failed", f"Could not create shortcut: {msg}", parent=self.root)

    def _launch_standalone_app(self):
        target = Path("tapp.exe")
        if not target.exists():
            target = Path("TappActionApp") / "publish" / "tapp.exe"
        if not target.exists():
            messagebox.showwarning("Not Found", "Please click 'Build Standalone tapp.exe' first.", parent=self.root)
            return

        try:
            subprocess.Popen([str(target.resolve())], cwd=str(target.parent.resolve()))
            messagebox.showinfo("Launched", "tapp.exe has been launched and is active in your System Tray.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to start tapp.exe:\n{e}", parent=self.root)

    def _import_calibration_dialog(self):
        path = filedialog.askopenfilename(
            title="Import Calibration JSON",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            initialdir=str(Path("data").resolve()) if Path("data").exists() else ".",
        )
        if not path:
            return
        res = load_calibration(Path(path))
        if res is not None:
            cfg, data = res
            self.calibrated_config = cfg
            self.detector.config = cfg
            self.last_calibration_summary = data.get("summary")
            messagebox.showinfo(
                "Calibration Imported",
                f"Successfully loaded calibration from:\n{path}\n\n"
                f"- Amplitude Floor: {cfg.amplitude_floor:.3f}\n"
                f"- Max Centroid: {cfg.centroid_max_hz:.0f} Hz\n"
                f"- Attack Ratio: {cfg.attack_ratio_threshold:.1f}x\n\n"
                "Profile loaded! You can test it on Step 1, or jump directly to Step 5 to build tapp.exe.",
            )
            if hasattr(self, "step0_status_lbl"):
                self.step0_status_lbl.config(
                    text=f"Loaded calibration from {Path(path).name} (Floor: {cfg.amplitude_floor:.3f}).",
                    fg=FG_COLOR,
                )
        else:
            messagebox.showerror("Import Error", f"Could not parse calibration JSON from:\n{path}")

    def _load_saved_calibration_and_notify(self):
        res = load_calibration()
        if res is not None:
            cfg, data = res
            self.calibrated_config = cfg
            self.detector.config = cfg
            self.last_calibration_summary = data.get("summary")
            messagebox.showinfo(
                "Default Profile Loaded",
                f"Successfully loaded data/calibration.json:\n\n"
                f"- Amplitude Floor: {cfg.amplitude_floor:.3f}\n"
                f"- Max Centroid: {cfg.centroid_max_hz:.0f} Hz\n"
                f"- Attack Ratio: {cfg.attack_ratio_threshold:.1f}x\n",
            )
            if hasattr(self, "step0_status_lbl"):
                self.step0_status_lbl.config(
                    text=f"Loaded default calibration.json (Floor: {cfg.amplitude_floor:.3f}).",
                    fg=FG_COLOR,
                )
        else:
            messagebox.showinfo("Not Found", "No data/calibration.json was found. Please run the calibration wizard.")

    def _export_calibration_dialog(self):
        path = filedialog.asksaveasfilename(
            title="Export Calibration JSON",
            defaultextension=".json",
            initialfile="calibration.json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
        )
        if not path:
            return
        try:
            cfg = self.calibrated_config or self.detector.config
            dev_meta = {
                "device_name": getattr(self, "selected_device_name", "Microphone Array"),
                "sample_rate": self.audio_engine.actual_sample_rate,
                "host_api": "Windows Sound Architecture",
            }
            save_calibration(cfg, self.last_calibration_summary or {}, dev_meta, filepath=Path(path))
            messagebox.showinfo(
                "Export Complete",
                f"Calibration JSON successfully saved to:\n{path}",
            )
            if hasattr(self, "build_status_lbl"):
                self.build_status_lbl.config(
                    text=f"Exported calibration to: {Path(path).name}",
                    fg=FG_COLOR,
                )
        except Exception as exc:
            messagebox.showerror("Export Error", f"Failed to export calibration:\n{exc}")

    def _cancel_active_recording(self):
        self.recording_stop_event.set()
        self.is_recording_phase = False
        self._show_step(self.current_step)

    def _finish_phase_a_early(self):
        self.recording_stop_event.set()

    def _finish_phase_b_early(self):
        self.recording_stop_event.set()

    def _finish_phase_c_early(self):
        self.recording_stop_event.set()

    # -------------------------------------------------------------------------
    # Advanced Lab Mode
    # -------------------------------------------------------------------------

    def _toggle_lab_mode(self):
        if not self.is_lab_mode:
            self.wizard_frame.pack_forget()
            self.lab_frame.pack(fill="both", expand=True)
            self.btn_toggle_lab.config(text="[ Back to Guided Wizard ]")
            self.is_lab_mode = True
        else:
            self.lab_frame.pack_forget()
            self.wizard_frame.pack(fill="both", expand=True)
            self.btn_toggle_lab.config(text="[ Open Advanced Lab ]")
            self.is_lab_mode = False

    def _build_advanced_lab(self):
        pad = tk.Frame(self.lab_frame, bg=BG_COLOR, padx=12, pady=10)
        pad.pack(fill="both", expand=True)

        # Hit banner
        self.hit_banner = tk.Label(
            pad,
            text="[ IDLE - LISTENING FOR TAPS ]",
            font=FONT_BIG,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            pady=12,
        )
        self.hit_banner.pack(fill="x", pady=(0, 8))
        self.knock_banner = self.hit_banner

        # Lab Controls Row
        ctrl_bar = tk.Frame(pad, bg=BG_COLOR)
        ctrl_bar.pack(fill="x", pady=(0, 8))

        self.btn_live_toggle = tk.Button(ctrl_bar, text="Start Live Detection", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=self._toggle_live_detection)
        self.btn_live_toggle.pack(side="left", padx=4)

        btn_clear = tk.Button(ctrl_bar, text="Clear Log", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=self._clear_live_log)
        btn_clear.pack(side="left", padx=4)

        btn_export = tk.Button(ctrl_bar, text="Export Evaluation Report", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=self._export_evaluation_report)
        btn_export.pack(side="left", padx=4)

        # Ground Truth Labeling Buttons
        fb_frame = tk.LabelFrame(pad, text=" Ground Truth Labeling (Select any event below) ", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, padx=8, pady=4)
        fb_frame.pack(fill="x", pady=(0, 8))

        btn_tp = tk.Button(fb_frame, text="True Positive", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=lambda: self._record_live_feedback("TP"))
        btn_tp.pack(side="left", padx=4, pady=2)

        btn_fp = tk.Button(fb_frame, text="False Positive", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=lambda: self._record_live_feedback("FP"))
        btn_fp.pack(side="left", padx=4, pady=2)

        btn_fn = tk.Button(fb_frame, text="Missed Tap (FN)", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, command=lambda: self._record_live_feedback("FN"))
        btn_fn.pack(side="left", padx=4, pady=2)

        # Event Listbox
        list_frame = tk.Frame(pad, bg=BG_COLOR)
        list_frame.pack(fill="both", expand=True)

        scroll = tk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="y")

        self.event_log_list = tk.Listbox(
            list_frame,
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
            selectbackground=HIGHLIGHT_BG,
            selectforeground=HIGHLIGHT_FG,
            yscrollcommand=scroll.set,
            bd=1,
            relief="solid",
        )
        self.event_log_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.event_log_list.yview)

    # -------------------------------------------------------------------------
    # Audio & Device Callbacks
    # -------------------------------------------------------------------------

    def _refresh_devices(self):
        devices, def_idx = list_input_devices()
        names = [f"{d.index}: {d.name} ({d.hostapi_name})" for d in devices]
        if hasattr(self, "device_combo"):
            self.device_combo["values"] = names
            if names:
                target_idx = 0
                if def_idx is not None:
                    for i, d in enumerate(devices):
                        if d.index == def_idx:
                            target_idx = i
                            break
                self.device_combo.current(target_idx)
                self._on_device_selected(None)

    def _on_device_selected(self, _event):
        sel = self.device_combo.get()
        if not sel:
            return
        try:
            idx = int(sel.split(":")[0])
            ok = self.audio_engine.start(device_index=idx)
            if not ok and hasattr(self, "status_lbl"):
                self.status_lbl.config(text=f"Microphone error: {self.audio_engine.error_message}")
            elif hasattr(self, "status_lbl"):
                self.status_lbl.config(text=f"Listening on device [{idx}]. Audio active.")
        except Exception as e:
            if hasattr(self, "status_lbl"):
                self.status_lbl.config(text=f"Error starting device: {e}")
        if hasattr(self, "btn_audio_toggle"):
            self.btn_audio_toggle.config(text="Pause Audio" if self.audio_engine.is_running else "Resume Audio")

    def _toggle_audio_stream(self):
        if self.audio_engine.is_running:
            self.audio_engine.stop()
            if hasattr(self, "btn_audio_toggle"):
                self.btn_audio_toggle.config(text="Resume Audio")
            if hasattr(self, "status_lbl"):
                self.status_lbl.config(text="Audio capture paused.")
        else:
            sel = self.device_combo.get() if hasattr(self, "device_combo") else ""
            idx = int(sel.split(":")[0]) if ":" in sel else None
            ok = self.audio_engine.start(device_index=idx)
            if hasattr(self, "btn_audio_toggle"):
                self.btn_audio_toggle.config(text="Pause Audio" if ok else "Resume Audio")
            if hasattr(self, "status_lbl"):
                self.status_lbl.config(text="Audio capture running." if ok else f"Audio start failed: {self.audio_engine.error_message}")

    def _on_audio_block(self, block: np.ndarray, current_sec: float):
        # Process detector if in live detecting mode OR on Step 0 for test feedback
        if self.is_live_detecting or self.current_step == 0:
            event = self.detector.process_block(block, current_sec)
            if event is not None:
                self.ui_update_queue.put(("HIT", event))

    def _on_action_fired(self, gesture: str, action: str):
        self.ui_update_queue.put(("ACTION", (gesture, action)))

    # -------------------------------------------------------------------------
    # Phase A, B, C Background Recorders
    # -------------------------------------------------------------------------

    def _start_record_phase_a(self):
        if self.is_recording_phase:
            return
        self.is_recording_phase = True
        self.recording_stop_event.clear()
        self.step1_status_lbl.config(text="Recording 10s of ambient noise... Please stay quiet.", fg=FG_COLOR)

        if hasattr(self, "step1_right_nav"):
            for child in self.step1_right_nav.winfo_children():
                child.destroy()
            tk.Button(
                self.step1_right_nav,
                text="Finish Early",
                font=FONT_BODY,
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
                padx=12,
                pady=6,
                command=self._finish_phase_a_early,
            ).pack(side="right", padx=4)
            tk.Button(
                self.step1_right_nav,
                text="Redo / Cancel",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                padx=12,
                pady=6,
                command=self._cancel_active_recording,
            ).pack(side="right", padx=4)

        def worker():
            dur = 10.0
            start = time.time()
            while (time.time() - start) < dur:
                if self.recording_stop_event.is_set():
                    break
                time.sleep(0.05)
                el = time.time() - start
                self.ui_update_queue.put(("PROG_A", (el, dur)))
            rec = self.audio_engine.read_recent_seconds(min(dur, max(2.0, time.time() - start)))
            self.ui_update_queue.put(("DONE_A", rec))

        threading.Thread(target=worker, daemon=True).start()

    def _start_record_phase_b(self):
        if self.is_recording_phase:
            return
        self.is_recording_phase = True
        self.recording_stop_event.clear()
        self.step2_status_lbl.config(text="Recording taps... Please tap right palm rest ~30 times with pauses.", fg=FG_COLOR)

        if hasattr(self, "step2_right_nav"):
            for child in self.step2_right_nav.winfo_children():
                child.destroy()
            tk.Button(
                self.step2_right_nav,
                text="Finish Early & Process",
                font=FONT_BIG,
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
                padx=14,
                pady=6,
                command=self._finish_phase_b_early,
            ).pack(side="right", padx=4)
            tk.Button(
                self.step2_right_nav,
                text="Redo / Cancel",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                padx=12,
                pady=6,
                command=self._cancel_active_recording,
            ).pack(side="right", padx=4)

        def worker():
            dur = 30.0
            start = time.time()
            while (time.time() - start) < dur:
                if self.recording_stop_event.is_set():
                    break
                time.sleep(0.05)
                el = time.time() - start
                self.ui_update_queue.put(("PROG_B", (el, dur)))
            rec = self.audio_engine.read_recent_seconds(min(dur, max(2.0, time.time() - start)))
            self.ui_update_queue.put(("DONE_B", rec))

        threading.Thread(target=worker, daemon=True).start()

    def _start_record_phase_c(self):
        if self.is_recording_phase:
            return
        self.is_recording_phase = True
        self.recording_stop_event.clear()
        self.step3_status_lbl.config(text="Recording typing and clicks... Please type and click normally.", fg=FG_COLOR)

        if hasattr(self, "step3_right_nav"):
            for child in self.step3_right_nav.winfo_children():
                child.destroy()
            tk.Button(
                self.step3_right_nav,
                text="Finish Early & Process",
                font=FONT_BIG,
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
                padx=14,
                pady=6,
                command=self._finish_phase_c_early,
            ).pack(side="right", padx=4)
            tk.Button(
                self.step3_right_nav,
                text="Redo / Cancel",
                font=FONT_BODY,
                bg=BG_COLOR,
                fg=FG_COLOR,
                padx=12,
                pady=6,
                command=self._cancel_active_recording,
            ).pack(side="right", padx=4)

        def worker():
            dur = 30.0
            start = time.time()
            while (time.time() - start) < dur:
                if self.recording_stop_event.is_set():
                    break
                time.sleep(0.05)
                el = time.time() - start
                self.ui_update_queue.put(("PROG_C", (el, dur)))
            rec = self.audio_engine.read_recent_seconds(min(dur, max(2.0, time.time() - start)))
            self.ui_update_queue.put(("DONE_C", rec))

        threading.Thread(target=worker, daemon=True).start()

    def _process_phase_a_result(self, audio: np.ndarray):
        try:
            self.ambient_audio = audio
        finally:
            self.is_recording_phase = False
            self._display_step1_completed()

    def _process_phase_b_result(self, audio: np.ndarray):
        try:
            sr = self.audio_engine.actual_sample_rate
            noise_floor = float(np.percentile(np.abs(self.ambient_audio), 99)) if self.ambient_audio is not None else 0.005
            cands = segment_audio_recording(
                audio,
                sr=sr,
                ambient_noise_level=noise_floor,
                default_label="tap",
                top_k=30,
            )
            self.tap_candidates = cands
        except Exception:
            self.tap_candidates = []
        finally:
            self.is_recording_phase = False
            self._display_step2_completed()

    def _process_phase_c_result(self, audio: np.ndarray):
        try:
            sr = self.audio_engine.actual_sample_rate
            noise_floor = float(np.percentile(np.abs(self.ambient_audio), 99)) if self.ambient_audio is not None else 0.005
            cands = segment_audio_recording(
                audio,
                sr=sr,
                ambient_noise_level=noise_floor,
                default_label="negative",
            )
            self.negative_candidates = cands
        except Exception:
            self.negative_candidates = []
        finally:
            self.is_recording_phase = False
            self._display_step3_completed()

    # -------------------------------------------------------------------------
    # Live Detection & Lab Log
    # -------------------------------------------------------------------------

    def _toggle_live_detection(self):
        if self.is_live_detecting:
            self.is_live_detecting = False
            self.btn_live_toggle.config(text="Start Live Detection", bg=BG_COLOR, fg=FG_COLOR)
            self._reset_hit_banner()
            self.status_lbl.config(text="Live detection stopped.")
        else:
            self.is_live_detecting = True
            self.live_session_start_time = time.time()
            self.btn_live_toggle.config(text="Stop Live Detection", bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
            self.status_lbl.config(text="Live detection running. Tap palm-rest or type to test.")

    def _on_hit_event(self, event: DetectedEvent):
        self.live_events.append(event)

        # High-contrast flash on banner
        if hasattr(self, "hit_banner"):
            self.hit_banner.config(text=f"[ HIT DETECTED! (Confidence: {event.confidence:.0%}) ]", bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
            self.root.after(450, self._reset_hit_banner)

        if hasattr(self, "step0_status_lbl") and self.current_step == 0:
            self.step0_status_lbl.config(
                text=f"Palm-rest tap detected! (Peak: {event.peak_amplitude:.3f}, Conf: {event.confidence:.0%})",
                fg=FG_COLOR,
            )
            self.root.after(1400, self._reset_step0_status)

        if hasattr(self, "action_manager"):
            self.action_manager.on_hit_detected()

        self.live_log_entries.append({"type": "HIT", "event": event})
        try:
            save_live_ground_truth_event(event)
        except Exception:
            pass

        time_str = time.strftime("%H:%M:%S", time.localtime(event.wall_clock_time))
        entry_line = (
            f"[{time_str}] HIT | Conf: {event.confidence:.0%} | Peak: {event.peak_amplitude:.3f} | "
            f"Hz: {event.spectral_centroid_hz:.0f} | Hi/Lo: {event.high_low_ratio:.2f} | Status: [PENDING REVIEW]"
        )
        if hasattr(self, "event_log_list"):
            self.event_log_list.insert(tk.END, entry_line)
            self.event_log_list.see(tk.END)

    def _reset_step0_status(self):
        if hasattr(self, "step0_status_lbl") and self.current_step == 0:
            self.step0_status_lbl.config(
                text="Microphone listening... Tap the palm rest or speak to test responsiveness.",
                fg=MUTED_FG,
            )

    def _reset_hit_banner(self):
        if hasattr(self, "hit_banner"):
            if not self.is_live_detecting:
                self.hit_banner.config(text="[ IDLE - LISTENING FOR TAPS ]", bg=BG_COLOR, fg=FG_COLOR)
            else:
                self.hit_banner.config(text="[ LISTENING... WAITING FOR TAP ]", bg=BG_COLOR, fg=FG_COLOR)

    _on_knock_event = _on_hit_event
    _reset_knock_banner = _reset_hit_banner

    def _record_live_feedback(self, feedback_type: str):
        if feedback_type == "FN":
            self.evaluation_metrics.false_negatives += 1
            now_str = time.strftime("%H:%M:%S")
            missed_line = f"[{now_str}] MISSED TAP | User tapped chassis but detector missed | Status: [FALSE NEGATIVE]"
            self.live_log_entries.append({"type": "FN", "timestamp": now_str})
            self.event_log_list.insert(tk.END, missed_line)
            self.event_log_list.see(tk.END)
            return

        sel = self.event_log_list.curselection()
        target_idx = None
        if sel:
            idx = sel[0]
            if 0 <= idx < len(self.live_log_entries) and self.live_log_entries[idx]["type"] in ("HIT", "KNOCK"):
                target_idx = idx

        if target_idx is None:
            for i in reversed(range(len(self.live_log_entries))):
                if self.live_log_entries[i]["type"] in ("HIT", "KNOCK"):
                    ev = self.live_log_entries[i]["event"]
                    if ev.user_feedback is None:
                        target_idx = i
                        break

        if target_idx is None:
            for i in reversed(range(len(self.live_log_entries))):
                if self.live_log_entries[i]["type"] in ("HIT", "KNOCK"):
                    target_idx = i
                    break

        if target_idx is not None:
            event = self.live_log_entries[target_idx]["event"]
            event.user_feedback = feedback_type
            time_str = time.strftime("%H:%M:%S", time.localtime(event.wall_clock_time))
            status_tag = "[TRUE POSITIVE]" if feedback_type == "TP" else "[FALSE POSITIVE]"
            updated_line = (
                f"[{time_str}] HIT | Conf: {event.confidence:.0%} | Peak: {event.peak_amplitude:.3f} | "
                f"Hz: {event.spectral_centroid_hz:.0f} | Hi/Lo: {event.high_low_ratio:.2f} | Status: {status_tag}"
            )
            self.event_log_list.delete(target_idx)
            self.event_log_list.insert(target_idx, updated_line)
            self.event_log_list.selection_clear(0, tk.END)
            self.event_log_list.selection_set(target_idx)
            update_live_ground_truth_file(self.live_events)

    def _clear_live_log(self):
        self.live_events.clear()
        self.live_log_entries.clear()
        self.event_log_list.delete(0, tk.END)
        self.status_lbl.config(text="Live log cleared.")

    def _export_evaluation_report(self):
        meta = {
            "device_name": self.device_combo.get(),
            "sample_rate": self.audio_engine.actual_sample_rate,
            "host_api": "Windows Sound Architecture",
        }
        report_path = Path("reports") / "hit_evaluation_report.md"
        generate_markdown_report(self.evaluation_metrics, meta, output_path=report_path)
        messagebox.showinfo("Report Exported", f"Evaluation report exported to:\n{report_path.resolve()}", parent=self.root)

    # -------------------------------------------------------------------------
    # UI Loop & Visual Renderers
    # -------------------------------------------------------------------------

    def _ui_poll_loop(self):
        while not self.ui_update_queue.empty():
            try:
                msg_type, payload = self.ui_update_queue.get_nowait()
                if msg_type == "PROG_A":
                    el, tot = payload
                    if hasattr(self, "step1_progress"):
                        self.step1_progress["value"] = (el / tot) * 100
                        self.step1_status_lbl.config(text=f"Recording ambient noise: {el:.1f}s / {tot:.1f}s (Stay quiet...)")
                        if hasattr(self, "cal_canvas"):
                            self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
                elif msg_type == "DONE_A":
                    self._process_phase_a_result(payload)
                elif msg_type == "PROG_B":
                    el, tot = payload
                    if hasattr(self, "step2_progress"):
                        self.step2_progress["value"] = (el / tot) * 100
                        self.step2_status_lbl.config(text=f"Recording taps: {el:.1f}s / {tot:.1f}s (Tap right palm rest...)")
                        if hasattr(self, "cal_canvas"):
                            self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
                elif msg_type == "DONE_B":
                    self._process_phase_b_result(payload)
                elif msg_type == "PROG_C":
                    el, tot = payload
                    if hasattr(self, "step3_progress"):
                        self.step3_progress["value"] = (el / tot) * 100
                        self.step3_status_lbl.config(text=f"Recording typing: {el:.1f}s / {tot:.1f}s (Type & click...)")
                        if hasattr(self, "cal_canvas"):
                            self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
                elif msg_type == "DONE_C":
                    self._process_phase_c_result(payload)
                elif msg_type in ("HIT", "KNOCK"):
                    self._on_hit_event(payload)
                elif msg_type == "ACTION":
                    pass
            except queue.Empty:
                break

        # Render Live Visualizers on Step 0 and Wizard Steps 1, 2, 3
        try:
            if self.current_step == 0:
                if hasattr(self, "vu_canvas"):
                    pk = self.audio_engine.latest_peak
                    self.vu_canvas.delete("all")
                    w = self.vu_canvas.winfo_width()
                    h = self.vu_canvas.winfo_height()
                    if w > 1 and h > 1:
                        # Non-linear boost for clear visual presence
                        meter_val = min(1.0, max(0.0, np.sqrt(pk * 4.0))) if pk > 0.0003 else 0.0
                        bar_w = int(w * meter_val)
                        if bar_w > 0:
                            self.vu_canvas.create_rectangle(0, 0, bar_w, h, fill=HIGHLIGHT_BG, outline="")
                        fl_val = min(1.0, max(0.0, np.sqrt(self.detector.config.amplitude_floor * 4.0)))
                        fl_x = int(w * fl_val)
                        self.vu_canvas.create_line(fl_x, 0, fl_x, h, fill="#888888", width=2)
                    if hasattr(self, "vu_label"):
                        self.vu_label.config(text=f"Peak: {pk:.3f}")

                if hasattr(self, "wave_canvas"):
                    self._render_dual_waveform_and_spectrum(self.wave_canvas, getattr(self, "wave_freq_lbl", None))
            elif self.current_step in (1, 2, 3):
                if hasattr(self, "cal_canvas"):
                    self._render_dual_waveform_and_spectrum(self.cal_canvas, getattr(self, "cal_freq_lbl", None))
        except Exception:
            pass

        self.root.after(40, self._ui_poll_loop)

    def _render_dual_waveform(self, canvas: tk.Canvas, status_lbl: Optional[tk.Label] = None):
        """Render two smooth rolling time waveforms (Raw Audio + Chassis Tap Vibration)."""
        try:
            canvas.delete("all")
            w = canvas.winfo_width()
            h = canvas.winfo_height()
            if w <= 1 or h <= 1:
                return

            raw, chassis = self.audio_engine.get_dual_waveform_snapshot(0.70)
            dom_f = getattr(self.audio_engine, "latest_dominant_freq", 0.0)
            sc = getattr(self.audio_engine, "latest_centroid", 0.0)
            pk = self.audio_engine.latest_peak

            mid_y1 = h * 0.25
            mid_y2 = h * 0.75
            track_div_y = int(h * 0.50)

            # Horizontal divider between Wave 1 and Wave 2
            canvas.create_line(0, track_div_y, w, track_div_y, fill="#EEEEEE", dash=(2, 2))

            # Dashed zero baselines for Wave 1 and Wave 2
            canvas.create_line(0, mid_y1, w, mid_y1, fill="#F2F2F2")
            canvas.create_line(0, mid_y2, w, mid_y2, fill="#F2F2F2")

            # Track titles
            canvas.create_text(8, 4, anchor="nw", text="WAVE 1: RAW MICROPHONE AUDIO", font=("Segoe UI", 7, "bold"), fill=MUTED_FG)
            canvas.create_text(8, track_div_y + 4, anchor="nw", text="WAVE 2: CHASSIS TAP (<350 Hz)", font=("Segoe UI", 7, "bold"), fill=MUTED_FG)

            # -------------------------------------------------------------
            # Draw Rolling Time Waves across full width (Wave 1 & Wave 2)
            # -------------------------------------------------------------
            n_raw = len(raw)
            if n_raw >= 32 and w > 10:
                mx_raw = float(np.max(np.abs(raw)))
                mx_ch = float(np.max(np.abs(chassis)))

                gain1 = 30.0 if mx_raw < 0.005 else (12.0 if mx_raw < 0.03 else (4.5 if mx_raw < 0.12 else 1.8))
                gain2 = 30.0 if mx_ch < 0.005 else (12.0 if mx_ch < 0.03 else (4.5 if mx_ch < 0.12 else 1.8))

                amp_h1 = h * 0.22
                amp_h2 = h * 0.22

                pts1 = []
                pts2 = []

                for x in range(w):
                    s_idx = int(x * n_raw / w)
                    e_idx = min(n_raw, int((x + 1) * n_raw / w) + 1)

                    sub1 = raw[s_idx:e_idx]
                    val1 = float(np.mean(sub1)) if len(sub1) > 0 else 0.0
                    y1 = mid_y1 - (val1 * gain1 * amp_h1)
                    y1 = max(2.0, min(track_div_y - 2.0, y1))
                    pts1.extend([x, y1])

                    sub2 = chassis[s_idx:e_idx]
                    val2 = float(np.mean(sub2)) if len(sub2) > 0 else 0.0
                    y2 = mid_y2 - (val2 * gain2 * amp_h2)
                    y2 = max(track_div_y + 2.0, min(h - 2.0, y2))
                    pts2.extend([x, y2])

                if len(pts1) >= 4:
                    canvas.create_line(pts1, fill=FG_COLOR, width=1)
                if len(pts2) >= 4:
                    canvas.create_line(pts2, fill=FG_COLOR, width=1.5)

            # -------------------------------------------------------------
            # Status / Readout Label
            # -------------------------------------------------------------
            if status_lbl is not None:
                if pk > 0.015:
                    if dom_f < 380:
                        band = "Chassis Tap Resonance"
                    elif dom_f > 1400:
                        band = "High Airborne / Typing Click"
                    else:
                        band = "Voice / Mid Acoustic"
                    status_lbl.config(
                        text=f"Peak: {pk:.3f} | Dominant Frequency: {dom_f:.0f} Hz | Centroid: {sc:.0f} Hz | Band: {band}",
                        fg=FG_COLOR,
                    )
                else:
                    status_lbl.config(
                        text=f"Peak: {pk:.3f} | Dominant Frequency: Idle | Centroid: {sc:.0f} Hz | Ambient Room Sound",
                        fg=MUTED_FG,
                    )
        except Exception:
            pass

    _render_dual_waveform_and_spectrum = _render_dual_waveform
    _render_waveform = _render_dual_waveform
    _render_dual_monitor = _render_dual_waveform

    def _build_status_bar(self):
        status_bar = tk.Frame(self.root, bg=BG_COLOR, bd=0, highlightthickness=1, highlightbackground=LIGHT_BORDER)
        status_bar.pack(fill="x", side="bottom", padx=14, pady=(0, 8))

        self.status_lbl = tk.Label(status_bar, text="Ready. Audio capture active.", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG, anchor="w")
        self.status_lbl.pack(side="left", padx=8, pady=4)

    def _load_saved_calibration_if_exists(self):
        cfg, summary = load_calibration()
        if cfg:
            self.calibrated_config = cfg
            self.detector.config = cfg

    def _load_saved_live_ground_truth_if_exists(self):
        saved = load_live_ground_truth_events()
        if saved:
            self.live_events = saved
            for ev in saved:
                self.live_log_entries.append({"type": "HIT", "event": ev})
                time_str = time.strftime("%H:%M:%S", time.localtime(ev.wall_clock_time))
                tag = f"[{ev.user_feedback}]" if ev.user_feedback else "[PENDING REVIEW]"
                line = (
                    f"[{time_str}] HIT | Conf: {ev.confidence:.0%} | Peak: {ev.peak_amplitude:.3f} | "
                    f"Hz: {ev.spectral_centroid_hz:.0f} | Hi/Lo: {ev.high_low_ratio:.2f} | Status: {tag}"
                )
                self.event_log_list.insert(tk.END, line)

    def _on_close(self):
        self.audio_engine.stop()
        self.root.destroy()


# Backward-compatibility alias
NokAppGUI = TappAppGUI
