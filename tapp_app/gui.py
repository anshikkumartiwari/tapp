"""Tkinter graphical user interface for Tapp Hit Detection Test App.

Minimal, clean design with default white background and black text.
Provides device selection, guided 4-phase calibration, real-time live detection,
and scientific metrics evaluation.
"""

from collections import deque
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Dict, List, Optional

import numpy as np

from .audio_engine import AudioCaptureEngine, AudioDeviceInfo, list_input_devices
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
from .segmenter import CandidateEvent, segment_audio_recording, save_candidate_wav
from .actions import AVAILABLE_ACTIONS, ActionSettings, GestureActionManager, execute_action


# Styling constants: Minimal white background, black text
BG_COLOR = "#FFFFFF"
FG_COLOR = "#000000"
MUTED_FG = "#555555"
BORDER_COLOR = "#000000"
LIGHT_BORDER = "#D0D0D0"
CARD_BG = "#FFFFFF"
ACTIVE_BG = "#EAEAEA"
HIGHLIGHT_BG = "#000000"
HIGHLIGHT_FG = "#FFFFFF"

FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_HEADING = ("Segoe UI", 11, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_BIG = ("Segoe UI", 16, "bold")


class TappAppGUI:
    """Main Application GUI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Tapp — Palm-Rest Hit Detection Test Suite")
        self.root.geometry("1020x760")
        self.root.minsize(920, 680)
        self.root.configure(bg=BG_COLOR)

        # Core Engines
        self.audio_engine = AudioCaptureEngine()
        self.detector = RealtimeHitDetector()
        self.audio_engine.add_consumer(self._on_audio_block)

        # Calibration Data State
        self.ambient_audio: Optional[np.ndarray] = None
        self.tap_candidates: List[CandidateEvent] = []
        self.negative_candidates: List[CandidateEvent] = []
        self.calibrated_config: Optional[DetectorConfig] = None
        self.last_calibration_summary: Optional[Dict] = None

        # Live Evaluation State
        self.live_events: List[DetectedEvent] = []
        self.live_log_entries: List[Dict] = []
        self.evaluation_metrics = EvaluationMetrics()
        self.live_session_start_time = 0.0
        self.negative_session_start_time = 0.0
        self.is_live_detecting = False
        self.is_alternating_test = False
        self.alternating_step = 0

        # Gesture Action Manager (Step 5)
        self.action_manager = GestureActionManager(on_action_fired=self._on_action_fired)
        self.action_manager.load_settings()

        # UI Poll Queue
        self.ui_update_queue: queue.Queue = queue.Queue()

        # Build UI layout
        self._build_header()
        self._build_tabs()
        self._build_status_bar()

        # Enumerate Devices and initialize
        self._refresh_devices()
        self._load_saved_calibration_if_exists()
        self._load_saved_live_ground_truth_if_exists()

        # Start periodic UI refresh loop
        self.root.after(40, self._ui_poll_loop)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------------------------------
    # Layout Builders
    # -------------------------------------------------------------------------

    def _build_header(self):
        header_frame = tk.Frame(self.root, bg=BG_COLOR, bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR)
        header_frame.pack(fill="x", padx=14, pady=(12, 6))

        top_bar = tk.Frame(header_frame, bg=BG_COLOR)
        top_bar.pack(fill="x", padx=12, pady=8)

        title_lbl = tk.Label(
            top_bar,
            text="TAPP — PALM-REST HIT DETECTION EXPERIMENT",
            font=FONT_TITLE,
            bg=BG_COLOR,
            fg=FG_COLOR,
        )
        title_lbl.pack(side="left")

        subtitle_lbl = tk.Label(
            top_bar,
            text="Lenovo IdeaPad Microphone Audio Experiment",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        )
        subtitle_lbl.pack(side="right")

    def _build_tabs(self):
        container = tk.Frame(self.root, bg=BG_COLOR)
        container.pack(fill="both", expand=True, padx=14, pady=6)

        # Tab button bar
        self.tab_bar = tk.Frame(container, bg=BG_COLOR)
        self.tab_bar.pack(fill="x", pady=(0, 6))

        self.tab_buttons = {}
        self.tab_frames = {}

        tabs = [
            ("devices", "1. Microphone & Audio Setup"),
            ("calibrate", "2. Guided Calibration Wizard"),
            ("live", "3. Live Detection & Ground Truth"),
            ("metrics", "4. Evaluation Metrics & Report"),
            ("actions", "5. Tap Action Mappings"),
        ]

        for tab_id, tab_label in tabs:
            btn = tk.Button(
                self.tab_bar,
                text=tab_label,
                font=FONT_HEADING,
                bg=BG_COLOR,
                fg=FG_COLOR,
                relief="flat",
                bd=1,
                highlightthickness=1,
                highlightbackground=LIGHT_BORDER,
                padx=10,
                pady=6,
                command=lambda tid=tab_id: self._switch_tab(tid),
            )
            btn.pack(side="left", padx=(0, 5))
            self.tab_buttons[tab_id] = btn

            frame = tk.Frame(container, bg=BG_COLOR, highlightthickness=1, highlightbackground=LIGHT_BORDER)
            self.tab_frames[tab_id] = frame

        # Build individual tabs
        self._build_tab_devices(self.tab_frames["devices"])
        self._build_tab_calibrate(self.tab_frames["calibrate"])
        self._build_tab_live(self.tab_frames["live"])
        self._build_tab_metrics(self.tab_frames["metrics"])
        self._build_tab_actions(self.tab_frames["actions"])

        self._switch_tab("devices")

    def _switch_tab(self, tab_id: str):
        for tid, frame in self.tab_frames.items():
            if tid == tab_id:
                frame.pack(fill="both", expand=True)
                self.tab_buttons[tid].configure(
                    bg=HIGHLIGHT_BG,
                    fg=HIGHLIGHT_FG,
                    highlightbackground=BORDER_COLOR,
                )
            else:
                frame.pack_forget()
                self.tab_buttons[tid].configure(
                    bg=BG_COLOR,
                    fg=FG_COLOR,
                    highlightbackground=LIGHT_BORDER,
                )

    def _build_status_bar(self):
        self.status_bar = tk.Frame(self.root, bg=BG_COLOR, bd=0, highlightthickness=1, highlightbackground=LIGHT_BORDER)
        self.status_bar.pack(fill="x", side="bottom", padx=14, pady=(4, 10))

        self.status_lbl = tk.Label(
            self.status_bar,
            text="Ready. Select microphone and start audio stream.",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
            anchor="w",
        )
        self.status_lbl.pack(side="left", padx=8, pady=4)

        self.stream_status_lbl = tk.Label(
            self.status_bar,
            text="MIC: STOPPED",
            font=("Segoe UI", 9, "bold"),
            bg=BG_COLOR,
            fg=MUTED_FG,
            anchor="e",
        )
        self.stream_status_lbl.pack(side="right", padx=8, pady=4)

    # -------------------------------------------------------------------------
    # Tab 1: Microphone & Audio Setup
    # -------------------------------------------------------------------------

    def _build_tab_devices(self, parent: tk.Frame):
        pad_frame = tk.Frame(parent, bg=BG_COLOR)
        pad_frame.pack(fill="both", expand=True, padx=14, pady=12)

        # Device Selection Card
        dev_card = tk.LabelFrame(
            pad_frame,
            text=" Audio Input Device Selection ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=10,
        )
        dev_card.pack(fill="x", pady=(0, 12))

        dev_row = tk.Frame(dev_card, bg=BG_COLOR)
        dev_row.pack(fill="x", pady=4)

        tk.Label(dev_row, text="Microphone:", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR).pack(side="left", padx=(0, 8))

        self.device_combo = ttk.Combobox(dev_row, width=70, state="readonly", font=FONT_BODY)
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        self.btn_refresh_devs = tk.Button(
            dev_row,
            text="Refresh",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._refresh_devices,
            padx=10,
        )
        self.btn_refresh_devs.pack(side="left", padx=4)

        ctrl_row = tk.Frame(dev_card, bg=BG_COLOR)
        ctrl_row.pack(fill="x", pady=(8, 2))

        self.btn_toggle_stream = tk.Button(
            ctrl_row,
            text="Start Audio Stream",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            command=self._toggle_audio_stream,
            padx=16,
            pady=6,
        )
        self.btn_toggle_stream.pack(side="left")

        self.dev_info_lbl = tk.Label(
            ctrl_row,
            text="Select device to inspect channels and sample rate.",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        )
        self.dev_info_lbl.pack(side="left", padx=16)

        # Real-time Audio Level & Waveform Monitor Card
        monitor_card = tk.LabelFrame(
            pad_frame,
            text=" Live Audio Monitor & VU Meter ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=10,
        )
        monitor_card.pack(fill="both", expand=True)

        meter_frame = tk.Frame(monitor_card, bg=BG_COLOR)
        meter_frame.pack(fill="x", pady=(0, 8))

        tk.Label(meter_frame, text="Peak Level:", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR).pack(side="left", padx=(0, 6))

        self.vu_canvas = tk.Canvas(meter_frame, height=22, bg=BG_COLOR, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self.vu_canvas.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.vu_text_lbl = tk.Label(meter_frame, text="Peak: 0.000 (-inf dB)", font=FONT_MONO, bg=BG_COLOR, fg=FG_COLOR, width=22)
        self.vu_text_lbl.pack(side="right")

        tk.Label(monitor_card, text="Rolling Input Activity History:", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(anchor="w", pady=(4, 2))

        self.waveform_canvas = tk.Canvas(
            monitor_card,
            bg=BG_COLOR,
            height=180,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
        )
        self.waveform_canvas.pack(fill="both", expand=True, pady=4)

    # -------------------------------------------------------------------------
    # Tab 2: Guided Calibration Wizard
    # -------------------------------------------------------------------------

    def _build_tab_calibrate(self, parent: tk.Frame):
        pad_frame = tk.Frame(parent, bg=BG_COLOR)
        pad_frame.pack(fill="both", expand=True, padx=14, pady=12)

        # Instructions banner
        instr_box = tk.LabelFrame(
            pad_frame,
            text=" Guided 4-Phase Calibration Instructions ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=12,
            pady=8,
        )
        instr_box.pack(fill="x", pady=(0, 10))

        instr_text = (
            "Target Area: Right-side palm rest near the Lenovo logo.\n"
            "• Phase A: 10s of quiet room ambience (do not tap or type).\n"
            "• Phase B: Tap the right palm-rest area ~30 times with deliberate taps and short pauses.\n"
            "• Phase C: 30-45s of realistic negative activities: typing on keyboard, trackpad clicks, occasional pauses.\n"
            "• Phase D: Review extracted candidate events, tune detection thresholds, and fit the lightweight classifier."
        )
        tk.Label(instr_box, text=instr_text, font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR, justify="left").pack(anchor="w")

        # 4 Phases Horizontal Cards
        phases_container = tk.Frame(pad_frame, bg=BG_COLOR)
        phases_container.pack(fill="x", pady=(0, 10))

        # Phase A
        self.card_a = tk.LabelFrame(
            phases_container,
            text=" Phase A: Ambient Noise ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        self.card_a.pack(side="left", fill="both", expand=True, padx=(0, 6))

        tk.Label(self.card_a, text="Record 10s room ambience.", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(anchor="w")
        self.lbl_status_a = tk.Label(self.card_a, text="Status: Not recorded", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR)
        self.lbl_status_a.pack(anchor="w", pady=4)

        self.btn_record_a = tk.Button(
            self.card_a,
            text="Record Ambient (10s)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._start_record_phase_a,
        )
        self.btn_record_a.pack(fill="x", pady=4)

        # Phase B
        self.card_b = tk.LabelFrame(
            phases_container,
            text=" Phase B: Palm-Rest Taps ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        self.card_b.pack(side="left", fill="both", expand=True, padx=4)

        tk.Label(self.card_b, text="Record ~30 deliberate taps (30s).", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(anchor="w")
        self.lbl_status_b = tk.Label(self.card_b, text="Taps Found: 0", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR)
        self.lbl_status_b.pack(anchor="w", pady=4)

        self.btn_record_b = tk.Button(
            self.card_b,
            text="Record Taps (30s)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._start_record_phase_b,
        )
        self.btn_record_b.pack(fill="x", pady=4)

        # Phase C
        self.card_c = tk.LabelFrame(
            phases_container,
            text=" Phase C: Negative Activities ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        self.card_c.pack(side="left", fill="both", expand=True, padx=(6, 0))

        tk.Label(self.card_c, text="Record typing & clicks (25s).", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(anchor="w")
        self.lbl_status_c = tk.Label(self.card_c, text="Negatives Found: 0", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR)
        self.lbl_status_c.pack(anchor="w", pady=4)

        self.btn_record_c = tk.Button(
            self.card_c,
            text="Record Typing/Clicks (25s)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._start_record_phase_c,
        )
        self.btn_record_c.pack(fill="x", pady=4)

        # Recording Progress bar
        prog_frame = tk.Frame(pad_frame, bg=BG_COLOR)
        prog_frame.pack(fill="x", pady=(0, 6))

        self.cal_prog_lbl = tk.Label(prog_frame, text="Idle", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG)
        self.cal_prog_lbl.pack(side="left")

        self.cal_progress = ttk.Progressbar(prog_frame, mode="determinate")
        self.cal_progress.pack(side="right", fill="x", expand=True, padx=(10, 0))

        # Live Audio & Frequency Monitor during calibration
        cal_mon_card = tk.LabelFrame(
            pad_frame,
            text=" Live Audio & Frequency Spectrum Monitor (Tap Zone is below the Frequency Cutoff Line) ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=8,
            pady=4,
        )
        cal_mon_card.pack(fill="x", pady=(0, 6))

        self.cal_freq_lbl = tk.Label(
            cal_mon_card,
            text="Peak: 0.000 (-inf dB) | Frequency: 0 Hz | Cutoff Line: 2200 Hz",
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=MUTED_FG,
            anchor="w",
        )
        self.cal_freq_lbl.pack(fill="x")

        self.cal_canvas = tk.Canvas(
            cal_mon_card,
            bg=BG_COLOR,
            height=100,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
        )
        self.cal_canvas.pack(fill="x", pady=(2, 0))

        # Phase D: Review and Candidate Inspection
        card_d = tk.LabelFrame(
            pad_frame,
            text=" Phase D: Candidate Event Review & Model Tuning ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=8,
        )
        card_d.pack(fill="both", expand=True)

        list_frame = tk.Frame(card_d, bg=BG_COLOR)
        list_frame.pack(fill="both", expand=True, pady=(0, 6))

        scroll = tk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="y")

        self.candidate_listbox = tk.Listbox(
            list_frame,
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
            selectbackground=HIGHLIGHT_BG,
            selectforeground=HIGHLIGHT_FG,
            yscrollcommand=scroll.set,
            bd=1,
            relief="solid",
            height=6,
        )
        self.candidate_listbox.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.candidate_listbox.yview)

        # Review Action Buttons
        act_row = tk.Frame(card_d, bg=BG_COLOR)
        act_row.pack(fill="x", pady=4)

        self.btn_mark_tap = tk.Button(
            act_row,
            text="Mark Selected as Intended Tap",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._mark_selected_as_tap,
            padx=8,
        )
        self.btn_mark_tap.pack(side="left", padx=(0, 4))

        self.btn_mark_typing = tk.Button(
            act_row,
            text="Mark Selected as Typing/Click",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._mark_selected_as_typing,
            padx=8,
        )
        self.btn_mark_typing.pack(side="left", padx=4)

        self.btn_mark_reject = tk.Button(
            act_row,
            text="Reject / Delete Candidate",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._reject_selected_candidate,
            padx=8,
        )
        self.btn_mark_reject.pack(side="left", padx=4)

        self.btn_calibrate_now = tk.Button(
            act_row,
            text="⚡ CALIBRATE & FIT DETECTOR",
            font=FONT_HEADING,
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
            bd=2,
            relief="solid",
            command=self._run_calibration_optimization,
            padx=14,
        )
        self.btn_calibrate_now.pack(side="right")

        self.lbl_cal_result = tk.Label(
            card_d,
            text="Detector thresholds not calibrated yet.",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
            anchor="w",
        )
        self.lbl_cal_result.pack(fill="x", pady=(4, 0))

    # -------------------------------------------------------------------------
    # Tab 3: Live Detection & Ground Truth
    # -------------------------------------------------------------------------

    def _build_tab_live(self, parent: tk.Frame):
        pad_frame = tk.Frame(parent, bg=BG_COLOR)
        pad_frame.pack(fill="both", expand=True, padx=14, pady=12)

        # BIG VISUAL HIT BANNER
        self.hit_banner = tk.Label(
            pad_frame,
            text="[ IDLE — LISTENING FOR TAPS ]",
            font=FONT_BIG,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            pady=16,
        )
        self.hit_banner.pack(fill="x", pady=(0, 10))
        self.knock_banner = self.hit_banner

        # Live Controls and Mode Bar
        ctrl_bar = tk.Frame(pad_frame, bg=BG_COLOR)
        ctrl_bar.pack(fill="x", pady=(0, 10))

        self.btn_live_toggle = tk.Button(
            ctrl_bar,
            text="▶ Start Live Detection",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            command=self._toggle_live_detection,
            padx=16,
            pady=6,
        )
        self.btn_live_toggle.pack(side="left", padx=(0, 10))

        self.btn_guided_test = tk.Button(
            ctrl_bar,
            text="Guided Alternating Test (Tap vs Type)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._toggle_guided_alternating_test,
            padx=12,
            pady=6,
        )
        self.btn_guided_test.pack(side="left", padx=(0, 10))

        self.live_session_info = tk.Label(
            ctrl_bar,
            text="Session Duration: 0.0s | Detected: 0",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        )
        self.live_session_info.pack(side="right")

        # Real-time Audio Level & Frequency Monitor on Live Detection Screen
        live_mon_card = tk.LabelFrame(
            pad_frame,
            text=" Real-Time Audio & Frequency Spectrum Monitor (Tap Zone is below the Cutoff Line) ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=4,
        )
        live_mon_card.pack(fill="x", pady=(0, 8))

        self.live_freq_lbl = tk.Label(
            live_mon_card,
            text="Peak: 0.000 (-inf dB) | Frequency: 0 Hz | Cutoff Line: 2200 Hz",
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=MUTED_FG,
            anchor="w",
        )
        self.live_freq_lbl.pack(fill="x")

        self.live_canvas = tk.Canvas(
            live_mon_card,
            bg=BG_COLOR,
            height=110,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
        )
        self.live_canvas.pack(fill="x", pady=(2, 0))

        # Ground Truth Feedback Buttons
        feedback_frame = tk.LabelFrame(
            pad_frame,
            text=" Ground Truth Evaluation Feedback (Label as you test) ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=8,
        )
        feedback_frame.pack(fill="x", pady=(0, 10))

        fb_btns_row = tk.Frame(feedback_frame, bg=BG_COLOR)
        fb_btns_row.pack(fill="x")

        self.btn_confirm_tp = tk.Button(
            fb_btns_row,
            text="✅ Mark as True Positive (TP)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._record_live_feedback("TP"),
            padx=10,
            pady=4,
        )
        self.btn_confirm_tp.pack(side="left", padx=(0, 6))

        self.btn_flag_fp = tk.Button(
            fb_btns_row,
            text="❌ Mark as False Alarm (FP)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._record_live_feedback("FP"),
            padx=10,
            pady=4,
        )
        self.btn_flag_fp.pack(side="left", padx=6)

        self.btn_record_fn = tk.Button(
            fb_btns_row,
            text="⚠️ Record Missed Tap (FN)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._record_live_feedback("FN"),
            padx=10,
            pady=4,
        )
        self.btn_record_fn.pack(side="left", padx=6)

        self.btn_clear_log_only = tk.Button(
            fb_btns_row,
            text="🧹 Clear Event Log Only",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._clear_live_event_log_only,
            padx=10,
            pady=4,
        )
        self.btn_clear_log_only.pack(side="left", padx=6)

        self.live_kpi_lbl = tk.Label(
            fb_btns_row,
            text="TP: 0 | FP: 0 | FN: 0 | Precision: 0.0% | Recall: 0.0%",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
        )
        self.live_kpi_lbl.pack(side="right")

        # Continuous Calibration & Batch Fine-Tuning Bar
        recal_frame = tk.Frame(pad_frame, bg=BG_COLOR)
        recal_frame.pack(fill="x", pady=(0, 8))

        self.btn_recalibrate = tk.Button(
            recal_frame,
            text="🔄 Fine-Tune Detector with Live Ground Truth",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            command=self._recalibrate_with_ground_truth,
            padx=12,
            pady=4,
        )
        self.btn_recalibrate.pack(side="left", padx=(0, 8))

        self.btn_import_log = tk.Button(
            recal_frame,
            text="📥 Paste / Import Event Log Lines",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._open_import_dialog,
            padx=10,
            pady=4,
        )
        self.btn_import_log.pack(side="left", padx=(0, 10))

        self.live_gt_summary_lbl = tk.Label(
            recal_frame,
            text="Persistent Ground Truth: 0 TPs | 0 FPs (Auto-saved to data/live_ground_truth.csv)",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=MUTED_FG,
        )
        self.live_gt_summary_lbl.pack(side="left")

        # Live Event Log
        log_frame = tk.LabelFrame(
            pad_frame,
            text=" Timestamped Detection Event Log (Select any event below to label it anytime) ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=6,
        )
        log_frame.pack(fill="both", expand=True)

        log_scroll = tk.Scrollbar(log_frame)
        log_scroll.pack(side="right", fill="y")

        self.event_log_list = tk.Listbox(
            log_frame,
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
            selectbackground=HIGHLIGHT_BG,
            selectforeground=HIGHLIGHT_FG,
            yscrollcommand=log_scroll.set,
            bd=1,
            relief="solid",
        )
        self.event_log_list.pack(side="left", fill="both", expand=True)
        log_scroll.config(command=self.event_log_list.yview)

    # -------------------------------------------------------------------------
    # Tab 4: Evaluation Metrics & Report
    # -------------------------------------------------------------------------

    def _build_tab_metrics(self, parent: tk.Frame):
        pad_frame = tk.Frame(parent, bg=BG_COLOR)
        pad_frame.pack(fill="both", expand=True, padx=14, pady=12)

        # KPI Metrics Cards Row
        kpi_row = tk.Frame(pad_frame, bg=BG_COLOR)
        kpi_row.pack(fill="x", pady=(0, 10))

        self.kpi_labels = {}
        metric_keys = [
            ("precision", "Precision", "0.0%"),
            ("recall", "Recall", "0.0%"),
            ("f1", "F1 Score", "0.000"),
            ("fp_min", "False Alarms / Min", "0.00"),
            ("latency", "Avg Latency", "0.0 ms"),
        ]

        for key, title, def_val in metric_keys:
            card = tk.Frame(kpi_row, bg=BG_COLOR, bd=1, relief="solid", padx=10, pady=8)
            card.pack(side="left", fill="both", expand=True, padx=4)

            tk.Label(card, text=title, font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(anchor="w")
            val_lbl = tk.Label(card, text=def_val, font=FONT_BIG, bg=BG_COLOR, fg=FG_COLOR)
            val_lbl.pack(anchor="w", pady=(4, 0))
            self.kpi_labels[key] = val_lbl

        # Confusion Matrix and Report Output
        report_frame = tk.LabelFrame(
            pad_frame,
            text=" Confusion Matrix & Evaluation Report ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=8,
        )
        report_frame.pack(fill="both", expand=True, pady=(0, 10))

        rep_scroll = tk.Scrollbar(report_frame)
        rep_scroll.pack(side="right", fill="y")

        self.report_text = tk.Text(
            report_frame,
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            yscrollcommand=rep_scroll.set,
        )
        self.report_text.pack(side="left", fill="both", expand=True)
        rep_scroll.config(command=self.report_text.yview)

        # Action Buttons Row
        btn_row = tk.Frame(pad_frame, bg=BG_COLOR)
        btn_row.pack(fill="x")

        self.btn_export_report = tk.Button(
            btn_row,
            text="Export Markdown & JSON Report",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            command=self._export_evaluation_report,
            padx=14,
            pady=6,
        )
        self.btn_export_report.pack(side="left")

        self.btn_clear_data = tk.Button(
            btn_row,
            text="Delete / Clear All Recorded Audio & Labels",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._delete_all_collected_data,
            padx=12,
            pady=6,
        )
        self.btn_clear_data.pack(side="right")

    # -------------------------------------------------------------------------
    # Tab 5: Tap Action Mappings (Volume Up, Volume Down, Mute, Media)
    # -------------------------------------------------------------------------

    def _build_tab_actions(self, parent: tk.Frame):
        pad_frame = tk.Frame(parent, bg=BG_COLOR)
        pad_frame.pack(fill="both", expand=True, padx=14, pady=12)

        # BIG ACTION STATUS BANNER
        self.action_banner = tk.Label(
            pad_frame,
            text="[ TAP GESTURE ACTIONS READY: Single Tap ➔ Vol Up | Double Tap ➔ Vol Down ]",
            font=FONT_BIG,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            pady=14,
        )
        self.action_banner.pack(fill="x", pady=(0, 10))

        # Master Enable Toggle
        enable_row = tk.Frame(pad_frame, bg=BG_COLOR)
        enable_row.pack(fill="x", pady=(0, 10))

        self.action_enable_var = tk.BooleanVar(value=self.action_manager.settings.enabled)
        self.chk_action_enable = tk.Checkbutton(
            enable_row,
            text=" Enable Tap Action Dispatching (Sends native Windows volume & media key presses)",
            variable=self.action_enable_var,
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            activebackground=BG_COLOR,
            activeforeground=FG_COLOR,
            command=self._on_action_settings_changed,
        )
        self.chk_action_enable.pack(side="left")

        # Gesture Mapping Config Card
        map_card = tk.LabelFrame(
            pad_frame,
            text=" Gesture to System Action Mappings ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=14,
            pady=10,
        )
        map_card.pack(fill="x", pady=(0, 10))

        # Single Tap Mapping
        st_row = tk.Frame(map_card, bg=BG_COLOR)
        st_row.pack(fill="x", pady=4)
        tk.Label(st_row, text="1. Single Tap Gesture:", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, width=22, anchor="w").pack(side="left")
        self.combo_single_tap = ttk.Combobox(st_row, values=AVAILABLE_ACTIONS, font=FONT_BODY, state="readonly", width=28)
        self.combo_single_tap.set(self.action_manager.settings.single_tap_action)
        self.combo_single_tap.pack(side="left", padx=8)
        self.combo_single_tap.bind("<<ComboboxSelected>>", lambda e: self._on_action_settings_changed())

        btn_test_single = tk.Button(
            st_row,
            text="🔊 Test Firing Single Tap Action",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._test_fire_action(self.combo_single_tap.get()),
            padx=8,
            pady=2,
        )
        btn_test_single.pack(side="left", padx=8)

        # Double Tap Mapping
        dt_row = tk.Frame(map_card, bg=BG_COLOR)
        dt_row.pack(fill="x", pady=4)
        tk.Label(dt_row, text="2. Double Tap Gesture:", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, width=22, anchor="w").pack(side="left")
        self.combo_double_tap = ttk.Combobox(dt_row, values=AVAILABLE_ACTIONS, font=FONT_BODY, state="readonly", width=28)
        self.combo_double_tap.set(self.action_manager.settings.double_tap_action)
        self.combo_double_tap.pack(side="left", padx=8)
        self.combo_double_tap.bind("<<ComboboxSelected>>", lambda e: self._on_action_settings_changed())

        btn_test_double = tk.Button(
            dt_row,
            text="🔉 Test Firing Double Tap Action",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._test_fire_action(self.combo_double_tap.get()),
            padx=8,
            pady=2,
        )
        btn_test_double.pack(side="left", padx=8)

        # Triple Tap Mapping
        tt_row = tk.Frame(map_card, bg=BG_COLOR)
        tt_row.pack(fill="x", pady=4)
        tk.Label(tt_row, text="3. Triple Tap Gesture:", font=FONT_HEADING, bg=BG_COLOR, fg=FG_COLOR, width=22, anchor="w").pack(side="left")
        self.combo_triple_tap = ttk.Combobox(tt_row, values=AVAILABLE_ACTIONS, font=FONT_BODY, state="readonly", width=28)
        self.combo_triple_tap.set(self.action_manager.settings.triple_tap_action)
        self.combo_triple_tap.pack(side="left", padx=8)
        self.combo_triple_tap.bind("<<ComboboxSelected>>", lambda e: self._on_action_settings_changed())

        btn_test_triple = tk.Button(
            tt_row,
            text="🔇 Test Firing Triple Tap Action",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=lambda: self._test_fire_action(self.combo_triple_tap.get()),
            padx=8,
            pady=2,
        )
        btn_test_triple.pack(side="left", padx=8)

        # Timing config & Save button row
        cfg_row = tk.Frame(map_card, bg=BG_COLOR)
        cfg_row.pack(fill="x", pady=(8, 2))

        tk.Label(cfg_row, text="Double-Tap Speed Window:", font=FONT_BODY, bg=BG_COLOR, fg=FG_COLOR).pack(side="left")
        self.spin_dt_timeout = tk.Spinbox(cfg_row, from_=0.20, to=0.80, increment=0.02, font=FONT_BODY, width=6, bd=1, relief="solid")
        self.spin_dt_timeout.delete(0, tk.END)
        self.spin_dt_timeout.insert(0, f"{self.action_manager.settings.double_tap_timeout_sec:.2f}")
        self.spin_dt_timeout.pack(side="left", padx=(6, 12))
        self.spin_dt_timeout.config(command=self._on_action_settings_changed)

        tk.Label(cfg_row, text="sec (Single tap waits this interval to confirm no 2nd tap follows; Double tap fires immediately)", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(side="left")

        btn_save_actions = tk.Button(
            cfg_row,
            text="💾 Save Action Settings",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            command=self._save_action_settings_manual,
            padx=12,
            pady=3,
        )
        btn_save_actions.pack(side="right")

        # Action Execution History Log Card
        hist_card = tk.LabelFrame(
            pad_frame,
            text=" Tap Action Execution History Log ",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=6,
        )
        hist_card.pack(fill="both", expand=True)

        hist_ctrl_row = tk.Frame(hist_card, bg=BG_COLOR)
        hist_ctrl_row.pack(fill="x", pady=(0, 4))

        tk.Label(hist_ctrl_row, text="Recent action executions triggered by palm-rest taps:", font=FONT_BODY, bg=BG_COLOR, fg=MUTED_FG).pack(side="left")

        btn_clear_hist = tk.Button(
            hist_ctrl_row,
            text="Clear Action History",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=self._clear_action_history,
            padx=8,
            pady=1,
        )
        btn_clear_hist.pack(side="right")

        act_scroll = tk.Scrollbar(hist_card)
        act_scroll.pack(side="right", fill="y")

        self.action_log_list = tk.Listbox(
            hist_card,
            font=FONT_MONO,
            bg=BG_COLOR,
            fg=FG_COLOR,
            selectbackground=HIGHLIGHT_BG,
            selectforeground=HIGHLIGHT_FG,
            yscrollcommand=act_scroll.set,
            bd=1,
            relief="solid",
        )
        self.action_log_list.pack(side="left", fill="both", expand=True)
        act_scroll.config(command=self.action_log_list.yview)

    def _on_action_settings_changed(self):
        try:
            dt_to = float(self.spin_dt_timeout.get())
        except (ValueError, TypeError):
            dt_to = 0.38

        self.action_manager.settings.enabled = self.action_enable_var.get()
        self.action_manager.settings.single_tap_action = self.combo_single_tap.get()
        self.action_manager.settings.double_tap_action = self.combo_double_tap.get()
        self.action_manager.settings.triple_tap_action = self.combo_triple_tap.get()
        self.action_manager.settings.double_tap_timeout_sec = dt_to
        self.action_manager.save_settings()

    def _save_action_settings_manual(self):
        self._on_action_settings_changed()
        messagebox.showinfo(
            "Settings Saved",
            f"Action settings saved to data/actions_config.json:\n\n"
            f"• Enabled: {self.action_manager.settings.enabled}\n"
            f"• Single Tap: {self.action_manager.settings.single_tap_action}\n"
            f"• Double Tap: {self.action_manager.settings.double_tap_action}\n"
            f"• Triple Tap: {self.action_manager.settings.triple_tap_action}\n"
            f"• Speed Window: {self.action_manager.settings.double_tap_timeout_sec}s",
        )

    def _test_fire_action(self, action_name: str):
        ok = execute_action(action_name)
        if ok:
            now_str = time.strftime("%H:%M:%S")
            self.action_log_list.insert(tk.END, f"[{now_str}] 🧪 TEST TRIGGER ➔ Executed: {action_name}")
            self.action_log_list.see(tk.END)
            self.status_lbl.config(text=f"Test fired action: {action_name}")

    def _clear_action_history(self):
        self.action_log_list.delete(0, tk.END)

    def _on_action_fired(self, gesture: str, action: str):
        self.ui_update_queue.put(("ACTION", (gesture, action)))

    def _handle_action_fired_ui(self, gesture: str, action: str):
        now_str = time.strftime("%H:%M:%S")
        log_line = f"[{now_str}] ⚡ {gesture.upper()} DETECTED ➔ Dispatched Action: {action}"
        self.action_log_list.insert(tk.END, log_line)
        self.action_log_list.see(tk.END)

        if hasattr(self, "action_banner"):
            self.action_banner.config(
                text=f"⚡ {gesture.upper()} ➔ {action}",
                bg=HIGHLIGHT_BG,
                fg=HIGHLIGHT_FG,
            )
            self.root.after(750, self._reset_action_banner)

        self.status_lbl.config(text=f"Tap Action Executed: {gesture} ➔ {action}")

    def _reset_action_banner(self):
        if hasattr(self, "action_banner"):
            self.action_banner.config(
                text="[ TAP GESTURE ACTIONS READY: Single Tap ➔ Vol Up | Double Tap ➔ Vol Down ]",
                bg=BG_COLOR,
                fg=FG_COLOR,
            )

    # -------------------------------------------------------------------------
    # Audio & Device Operations
    # -------------------------------------------------------------------------

    def _refresh_devices(self):
        devices, default_idx = list_input_devices()
        self.device_list = devices
        combo_values = [d.display_name() for d in devices]
        self.device_combo["values"] = combo_values

        if devices:
            default_combo_idx = 0
            for i, dev in enumerate(devices):
                if dev.index == default_idx:
                    default_combo_idx = i
                    break
            self.device_combo.current(default_combo_idx)
            self._update_device_info_display(devices[default_combo_idx])
        else:
            self.dev_info_lbl.config(text="No audio input devices found.")

    def _on_device_selected(self, event):
        idx = self.device_combo.current()
        if 0 <= idx < len(self.device_list):
            dev = self.device_list[idx]
            self._update_device_info_display(dev)
            if self.audio_engine.is_running:
                self.audio_engine.start(device_index=dev.index)

    def _update_device_info_display(self, dev: AudioDeviceInfo):
        self.dev_info_lbl.config(
            text=f"Selected: Device {dev.index} ({dev.name}) | {dev.hostapi_name} | {dev.max_input_channels} In | Default {dev.default_samplerate}Hz"
        )

    def _toggle_audio_stream(self):
        if self.audio_engine.is_running:
            self.audio_engine.stop()
            self.btn_toggle_stream.config(text="Start Audio Stream", bg=BG_COLOR, fg=FG_COLOR)
            self.stream_status_lbl.config(text="MIC: STOPPED", fg=MUTED_FG)
            self.status_lbl.config(text="Microphone stream stopped.")
        else:
            sel_idx = self.device_combo.current()
            dev_idx = self.device_list[sel_idx].index if (self.device_list and sel_idx >= 0) else None
            ok = self.audio_engine.start(device_index=dev_idx)
            if ok:
                self.btn_toggle_stream.config(text="Stop Audio Stream", bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
                self.stream_status_lbl.config(text=f"MIC: RUNNING ({self.audio_engine.actual_sample_rate}Hz)", fg=BORDER_COLOR)
                self.status_lbl.config(text=f"Capturing audio from device {self.audio_engine.device_index} @ {self.audio_engine.actual_sample_rate}Hz.")
            else:
                messagebox.showerror("Audio Error", self.audio_engine.error_message or "Failed to start audio stream.")

    def _on_audio_block(self, block: np.ndarray, current_sec: float):
        """Audio callback consumer: feed detector if live testing."""
        if self.is_live_detecting:
            event = self.detector.process_block(block, current_sec)
            if event is not None:
                self.ui_update_queue.put(("HIT", event))

    # -------------------------------------------------------------------------
    # Calibration Phases (A, B, C, D)
    # -------------------------------------------------------------------------

    def _start_record_phase_a(self):
        """Phase A: 10s of quiet ambient room noise."""
        if self.audio_engine.is_running:
            self.audio_engine.stop()
            self.btn_toggle_stream.config(text="Start Audio Stream", bg=BG_COLOR, fg=FG_COLOR)

        sel_idx = self.device_combo.current()
        dev_idx = self.device_list[sel_idx].index if self.device_list else None

        self.cal_prog_lbl.config(text="Phase A: Recording 10s of ambient silence... Stay quiet.")
        self.btn_record_a.config(state="disabled")

        def worker():
            cancel_ev = threading.Event()
            audio = self.audio_engine.record_duration(
                10.0,
                progress_callback=lambda el, tot: self.ui_update_queue.put(("PROG_A", (el, tot))),
                cancel_event=cancel_ev,
            )
            self.ui_update_queue.put(("DONE_A", audio))

        threading.Thread(target=worker, daemon=True).start()

    def _start_record_phase_b(self):
        """Phase B: 30s of deliberate palm-rest taps (~30 taps)."""
        if self.audio_engine.is_running:
            self.audio_engine.stop()
            self.btn_toggle_stream.config(text="Start Audio Stream", bg=BG_COLOR, fg=FG_COLOR)

        self.cal_prog_lbl.config(text="Phase B: Tap right palm-rest area ~30 times with short pauses (30s)...")
        self.btn_record_b.config(state="disabled")

        def worker():
            audio = self.audio_engine.record_duration(
                30.0,
                progress_callback=lambda el, tot: self.ui_update_queue.put(("PROG_B", (el, tot))),
            )
            self.ui_update_queue.put(("DONE_B", audio))

        threading.Thread(target=worker, daemon=True).start()

    def _start_record_phase_c(self):
        """Phase C: 25s of realistic negative activities (typing, clicks)."""
        if self.audio_engine.is_running:
            self.audio_engine.stop()
            self.btn_toggle_stream.config(text="Start Audio Stream", bg=BG_COLOR, fg=FG_COLOR)

        self.cal_prog_lbl.config(text="Phase C: Type normally on keyboard and click trackpad...")
        self.btn_record_c.config(state="disabled")

        def worker():
            audio = self.audio_engine.record_duration(
                25.0,
                progress_callback=lambda el, tot: self.ui_update_queue.put(("PROG_C", (el, tot))),
            )
            self.ui_update_queue.put(("DONE_C", audio))

        threading.Thread(target=worker, daemon=True).start()

    def _process_phase_a_result(self, audio: Optional[np.ndarray]):
        self.btn_record_a.config(state="normal")
        if audio is None:
            messagebox.showerror("Calibration Error", "Failed to record ambient audio.")
            return

        self.ambient_audio = audio
        pk = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio ** 2)))
        self.lbl_status_a.config(text=f"Status: Recorded (Peak: {pk:.4f}, RMS: {rms:.4f})")
        self.cal_prog_lbl.config(text="Phase A Complete! Ambient profile captured.")
        self.cal_progress["value"] = 100

        # Save raw ambient audio
        raw_path = Path("data") / "raw" / "ambient.wav"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        int16_audio = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        import scipy.io.wavfile as wavfile
        wavfile.write(str(raw_path), self.audio_engine.actual_sample_rate, int16_audio)

    def _process_phase_b_result(self, audio: Optional[np.ndarray]):
        self.btn_record_b.config(state="normal")
        if audio is None:
            messagebox.showerror("Calibration Error", "Failed to record taps audio.")
            return

        sr = self.audio_engine.actual_sample_rate
        amb_floor = float(np.max(np.abs(self.ambient_audio))) if self.ambient_audio is not None else 0.01

        # Segment candidates and pick all 30 strongest deliberate taps
        candidates = segment_audio_recording(
            audio,
            sr=sr,
            ambient_noise_level=amb_floor,
            default_label="tap",
            top_k=30,
        )

        for c in candidates:
            c.confirmed = True
            # Save candidate event clip
            ev_path = Path("data") / "events" / f"tap_event_{c.event_id:03d}.wav"
            save_candidate_wav(c, ev_path, sr)

        self.tap_candidates = candidates
        self.lbl_status_b.config(text=f"Taps Picked: {len(candidates)} / 30 strong taps")
        self.cal_prog_lbl.config(text=f"Phase B Complete! Extracted {len(candidates)} strongest deliberate taps.")
        self.cal_progress["value"] = 100
        self._refresh_candidate_list()

    def _process_phase_c_result(self, audio: Optional[np.ndarray]):
        self.btn_record_c.config(state="normal")
        if audio is None:
            messagebox.showerror("Calibration Error", "Failed to record negative activities audio.")
            return

        sr = self.audio_engine.actual_sample_rate
        amb_floor = float(np.max(np.abs(self.ambient_audio))) if self.ambient_audio is not None else 0.01

        # Segment candidates using the exact same detection pipeline
        candidates = segment_audio_recording(
            audio,
            sr=sr,
            ambient_noise_level=amb_floor,
            default_label="typing",
        )

        for c in candidates:
            c.confirmed = True
            ev_path = Path("data") / "events" / f"neg_event_{c.event_id:03d}.wav"
            save_candidate_wav(c, ev_path, sr)

        self.negative_candidates = candidates
        self.lbl_status_c.config(text=f"Negatives Found: {len(candidates)} events")
        self.cal_prog_lbl.config(text=f"Phase C Complete! Segmented {len(candidates)} typing/click events.")
        self.cal_progress["value"] = 100
        self._refresh_candidate_list()

    def _refresh_candidate_list(self):
        self.candidate_listbox.delete(0, tk.END)
        all_cands = self.tap_candidates + self.negative_candidates
        for i, c in enumerate(all_cands):
            self.candidate_listbox.insert(tk.END, f"[{i+1:02d}] {c.summary()}")

    def _mark_selected_as_tap(self):
        sel = self.candidate_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        all_cands = self.tap_candidates + self.negative_candidates
        if 0 <= idx < len(all_cands):
            c = all_cands[idx]
            c.label = "tap"
            self._refresh_candidate_list()
            self.candidate_listbox.selection_set(idx)

    def _mark_selected_as_typing(self):
        sel = self.candidate_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        all_cands = self.tap_candidates + self.negative_candidates
        if 0 <= idx < len(all_cands):
            c = all_cands[idx]
            c.label = "typing"
            self._refresh_candidate_list()
            self.candidate_listbox.selection_set(idx)

    def _reject_selected_candidate(self):
        sel = self.candidate_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.tap_candidates):
            del self.tap_candidates[idx]
        else:
            neg_idx = idx - len(self.tap_candidates)
            if 0 <= neg_idx < len(self.negative_candidates):
                del self.negative_candidates[neg_idx]
        self._refresh_candidate_list()

    def _run_calibration_optimization(self):
        """Compute optimal thresholds and train classifier."""
        if self.ambient_audio is None:
            # Create synthetic ambient if user calibrated without Phase A
            self.ambient_audio = np.random.normal(0, 0.005, 16000 * 5).astype(np.float32)

        if not self.tap_candidates:
            messagebox.showwarning("Calibration Incomplete", "Please complete Phase B (record taps) before calibrating.")
            return

        sr = self.audio_engine.actual_sample_rate
        try:
            cfg, summary = optimize_calibration(
                ambient_audio=self.ambient_audio,
                tap_candidates=self.tap_candidates,
                negative_candidates=self.negative_candidates,
                sr=sr,
            )
            self.calibrated_config = cfg
            self.last_calibration_summary = summary
            self.detector.update_config(cfg)

            # Persist calibration
            dev_info = {
                "device_index": self.audio_engine.device_index,
                "sample_rate": sr,
                "name": self.device_combo.get(),
            }
            save_calibration(cfg, summary, dev_info)

            # Export labels CSV
            all_cands = self.tap_candidates + self.negative_candidates
            export_labels_to_csv(all_cands)

            # Display results
            res_str = (
                f"Fitted Thresholds: Floor={cfg.amplitude_floor:.4f} | "
                f"AttackThr={cfg.attack_ratio_threshold:.1f}x | "
                f"CentroidMax={cfg.centroid_max_hz:.0f}Hz | "
                f"Hi/LoMax={cfg.high_low_ratio_max:.2f} | "
                f"Train Accuracy={summary.get('training_accuracy', 1.0):.1%}"
            )
            self.lbl_cal_result.config(text=res_str, fg=BORDER_COLOR)
            messagebox.showinfo(
                "Calibration Succeeded",
                f"Detector successfully calibrated and saved to data/calibration.json!\n\n{res_str}",
            )
        except Exception as exc:
            messagebox.showerror("Calibration Failed", f"Optimization error: {exc}")

    def _load_saved_calibration_if_exists(self):
        loaded = load_calibration()
        if loaded is not None:
            cfg, data = loaded
            self.calibrated_config = cfg
            self.detector.update_config(cfg)
            th = cfg
            self.lbl_cal_result.config(
                text=(
                    f"Loaded Saved Calibration: Floor={th.amplitude_floor:.4f} | "
                    f"AttackThr={th.attack_ratio_threshold:.1f}x | "
                    f"CentroidMax={th.centroid_max_hz:.0f}Hz | Hi/Lo={th.high_low_ratio_max:.2f}"
                ),
                fg=BORDER_COLOR,
            )
            # Match saved device
            dev_meta = data.get("device", {})
            saved_idx = dev_meta.get("device_index")
            if saved_idx is not None and hasattr(self, "device_list"):
                for i, dev in enumerate(self.device_list):
                    if dev.index == saved_idx:
                        self.device_combo.current(i)
                        self._update_device_info_display(dev)
                        break

            # Automatically resume on Step 3 (Live Detection)
            self._switch_tab("live")
            self.status_lbl.config(
                text="Calibration loaded from data/calibration.json! Resumed on Step 3 (Live Detection). Ready to test."
            )

    # -------------------------------------------------------------------------
    # Live Detection & Evaluation Mode
    # -------------------------------------------------------------------------

    def _toggle_live_detection(self):
        if self.is_live_detecting:
            self.is_live_detecting = False
            self.btn_live_toggle.config(text="▶ Start Live Detection", bg=BG_COLOR, fg=FG_COLOR)
            self.status_lbl.config(text="Live detection stopped.")
        else:
            if not self.audio_engine.is_running:
                ok = self.audio_engine.start()
                if not ok:
                    messagebox.showerror("Error", "Could not start microphone stream.")
                    return
                self.btn_toggle_stream.config(text="Stop Audio Stream", bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
                self.stream_status_lbl.config(text=f"MIC: RUNNING ({self.audio_engine.actual_sample_rate}Hz)", fg=BORDER_COLOR)

            self.is_live_detecting = True
            self.live_session_start_time = time.time()
            self.btn_live_toggle.config(text="⏹ Stop Live Detection", bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
            self.status_lbl.config(text="Live detection active! Tap right palm-rest or type to test.")

    def _on_hit_event(self, event: DetectedEvent):
        """Invoked when the detector identifies a hit event."""
        self.live_events.append(event)

        # Big Visual Flash on Banner
        self.hit_banner.config(
            text=f"⚡ HIT DETECTED! (Confidence: {event.confidence:.0%})",
            bg=HIGHLIGHT_BG,
            fg=HIGHLIGHT_FG,
        )
        self.root.after(450, self._reset_hit_banner)

        # Dispatch to Gesture Action Manager (Step 5)
        if hasattr(self, "action_manager"):
            self.action_manager.on_hit_detected()

        # Track in log entries and auto-persist to CSV
        self.live_log_entries.append({"type": "HIT", "event": event})
        try:
            save_live_ground_truth_event(event)
        except Exception:
            pass

        # Append to Event Listbox with explicit [PENDING REVIEW] status
        time_str = time.strftime("%H:%M:%S", time.localtime(event.wall_clock_time))
        entry_line = (
            f"[{time_str}] HIT | Conf: {event.confidence:.0%} | Peak: {event.peak_amplitude:.3f} | "
            f"Hz: {event.spectral_centroid_hz:.0f} | Hi/Lo: {event.high_low_ratio:.2f} | Status: [PENDING REVIEW]"
        )
        self.event_log_list.insert(tk.END, entry_line)
        self.event_log_list.see(tk.END)

        # Auto-increment if in guided alternating test
        if self.is_alternating_test and self.alternating_step % 2 == 1:
            self._record_live_feedback("TP")
        elif self.is_alternating_test and self.alternating_step % 2 == 0:
            self._record_live_feedback("FP")

        self._update_live_kpis()
        self._update_live_gt_summary()

    def _reset_hit_banner(self):
        if not self.is_live_detecting:
            self.hit_banner.config(text="[ IDLE — LISTENING FOR TAPS ]", bg=BG_COLOR, fg=FG_COLOR)
        else:
            self.hit_banner.config(text="[ LISTENING... WAITING FOR TAP ]", bg=BG_COLOR, fg=FG_COLOR)

    # Backward-compatibility aliases
    _on_knock_event = _on_hit_event
    _reset_knock_banner = _reset_hit_banner

    def _record_live_feedback(self, feedback_type: str):
        if feedback_type == "FN":
            self.evaluation_metrics.false_negatives += 1
            now_str = time.strftime("%H:%M:%S")
            missed_line = f"[{now_str}] MISSED TAP | User tapped chassis but detector missed | Status: ⚠️ FALSE NEGATIVE"
            self.live_log_entries.append({"type": "FN", "timestamp": now_str})
            self.event_log_list.insert(tk.END, missed_line)
            self.event_log_list.see(tk.END)
            self._update_live_kpis()
            self._update_live_gt_summary()
            return

        # Target an entry:
        # 1. If an item is selected in the listbox, use that item
        # 2. Otherwise find the most recent unreviewed HIT entry
        # 3. Otherwise find the last HIT entry
        sel = self.event_log_list.curselection()
        target_idx = None
        if sel:
            idx = sel[0]
            if 0 <= idx < len(self.live_log_entries) and self.live_log_entries[idx]["type"] in ("HIT", "KNOCK"):
                target_idx = idx

        if target_idx is None:
            # Look backwards for unreviewed hit
            for i in reversed(range(len(self.live_log_entries))):
                if self.live_log_entries[i]["type"] in ("HIT", "KNOCK"):
                    ev = self.live_log_entries[i]["event"]
                    if ev.user_feedback is None:
                        target_idx = i
                        break

        if target_idx is None:
            # Fallback to last hit
            for i in reversed(range(len(self.live_log_entries))):
                if self.live_log_entries[i]["type"] in ("HIT", "KNOCK"):
                    target_idx = i
                    break

        if target_idx is not None:
            event = self.live_log_entries[target_idx]["event"]
            event.user_feedback = feedback_type
            time_str = time.strftime("%H:%M:%S", time.localtime(event.wall_clock_time))
            status_tag = "✅ TRUE POSITIVE" if feedback_type == "TP" else "❌ FALSE POSITIVE"
            updated_line = (
                f"[{time_str}] HIT | Conf: {event.confidence:.0%} | Peak: {event.peak_amplitude:.3f} | "
                f"Hz: {event.spectral_centroid_hz:.0f} | Hi/Lo: {event.high_low_ratio:.2f} | Status: {status_tag}"
            )
            self.event_log_list.delete(target_idx)
            self.event_log_list.insert(target_idx, updated_line)
            self.event_log_list.selection_clear(0, tk.END)
            self.event_log_list.selection_set(target_idx)

            # Auto-update persistent ground truth CSV
            try:
                update_live_ground_truth_file(self.live_events)
            except Exception:
                pass

            # Recalculate metrics accurately
            self.evaluation_metrics.true_positives = sum(1 for e in self.live_events if e.user_feedback == "TP")
            self.evaluation_metrics.false_positives = sum(1 for e in self.live_events if e.user_feedback == "FP")
            self._update_live_kpis()
            self._update_live_gt_summary()

    def _clear_live_event_log_only(self):
        """Clear live detection log and metrics without touching calibration.json."""
        self.live_events = []
        self.live_log_entries = []
        self.evaluation_metrics = EvaluationMetrics()
        self.event_log_list.delete(0, tk.END)
        self.live_kpi_lbl.config(text="TP: 0 | FP: 0 | FN: 0 | Precision: 0.0% | Recall: 0.0%")
        self._update_metrics_tab()
        try:
            if LIVE_GROUND_TRUTH_FILE.exists():
                LIVE_GROUND_TRUTH_FILE.unlink()
        except Exception:
            pass
        self._update_live_gt_summary()
        self.status_lbl.config(text="Live event log and test metrics cleared. Calibration data preserved.")

    def _load_saved_live_ground_truth_if_exists(self):
        """Restore previous live ground-truth events from data/live_ground_truth.csv on startup."""
        saved_events = load_live_ground_truth_events()
        if not saved_events:
            return

        self.live_events = saved_events
        self.live_log_entries = []
        self.event_log_list.delete(0, tk.END)

        for ev in saved_events:
            self.live_log_entries.append({"type": "HIT", "event": ev})
            time_str = time.strftime("%H:%M:%S", time.localtime(ev.wall_clock_time))
            if ev.user_feedback == "TP":
                status_tag = "✅ TRUE POSITIVE"
            elif ev.user_feedback == "FP":
                status_tag = "❌ FALSE POSITIVE"
            elif ev.user_feedback == "FN":
                status_tag = "⚠️ FALSE NEGATIVE"
            else:
                status_tag = "[PENDING REVIEW]"

            entry_line = (
                f"[{time_str}] HIT | Conf: {ev.confidence:.0%} | Peak: {ev.peak_amplitude:.3f} | "
                f"Hz: {ev.spectral_centroid_hz:.0f} | Hi/Lo: {ev.high_low_ratio:.2f} | Status: {status_tag}"
            )
            self.event_log_list.insert(tk.END, entry_line)

        self.evaluation_metrics.true_positives = sum(1 for e in self.live_events if e.user_feedback == "TP")
        self.evaluation_metrics.false_positives = sum(1 for e in self.live_events if e.user_feedback == "FP")
        self.evaluation_metrics.false_negatives = sum(1 for e in self.live_events if e.user_feedback == "FN")
        self._update_live_kpis()
        self._update_live_gt_summary()

    def _update_live_gt_summary(self):
        tps = sum(1 for e in self.live_events if e.user_feedback == "TP")
        fps = sum(1 for e in self.live_events if e.user_feedback == "FP")
        pending = sum(1 for e in self.live_events if e.user_feedback is None)
        if hasattr(self, "live_gt_summary_lbl"):
            self.live_gt_summary_lbl.config(
                text=f"Live Ground Truth: {tps} TPs | {fps} FPs | {pending} Pending (Saved to data/live_ground_truth.csv)"
            )

    def _recalibrate_with_ground_truth(self):
        """Fine-tune thresholds and retrain classifier using live ground truth labels."""
        tps = sum(1 for e in self.live_events if e.user_feedback == "TP")
        fps = sum(1 for e in self.live_events if e.user_feedback == "FP")

        if tps == 0 and fps == 0:
            messagebox.showinfo(
                "Label Events First",
                "Please label at least one event in the listbox as:\n"
                "• ✅ True Positive (TP - deliberate palm-rest tap) or\n"
                "• ❌ False Alarm (FP - typing, click, or noise)\n\n"
                "Once labeled, click this button to automatically fine-tune the detector!"
            )
            return

        sr = self.audio_engine.actual_sample_rate
        old_cfg = self.detector.config

        try:
            new_cfg, summary = recalibrate_with_live_ground_truth(
                live_events=self.live_events,
                ambient_audio=self.ambient_audio,
                sr=sr,
            )
            self.detector.update_config(new_cfg)
            self.calibrated_config = new_cfg
            self.last_calibration_summary = summary

            # Show before / after comparison
            old_floor = old_cfg.amplitude_floor
            new_floor = new_cfg.amplitude_floor
            old_cut = old_cfg.centroid_max_hz
            new_cut = new_cfg.centroid_max_hz
            old_acc = self.last_calibration_summary.get("training_accuracy", 1.0) if self.last_calibration_summary else 1.0
            new_acc = summary.get("training_accuracy", 1.0)

            msg = (
                "🎯 Detector Fine-Tuned Successfully!\n\n"
                f"• Samples Used: {summary.get('total_taps')} Taps (+{summary.get('live_taps_added')} live) | "
                f"{summary.get('total_negatives')} Negatives (+{summary.get('live_negatives_added')} live)\n"
                f"• Amplitude Floor: {old_floor:.4f} ➔ {new_floor:.4f}\n"
                f"• Centroid Cutoff: {old_cut:.0f} Hz ➔ {new_cut:.0f} Hz\n"
                f"• Attack Ratio Threshold: {old_cfg.attack_ratio_threshold:.1f}x ➔ {new_cfg.attack_ratio_threshold:.1f}x\n"
                f"• Training Accuracy: {new_acc:.1%}\n\n"
                "New parameters are active in real time and saved to data/calibration.json."
            )
            messagebox.showinfo("Fine-Tuning Complete", msg)
            self.status_lbl.config(text=f"Detector fine-tuned with {tps} TPs and {fps} FPs! New accuracy: {new_acc:.1%}")
            self._update_live_gt_summary()
        except Exception as exc:
            messagebox.showerror("Fine-Tuning Error", f"Failed to fine-tune detector:\n{exc}")

    def _open_import_dialog(self):
        """Open a text dialog allowing user to paste raw log lines to recover or import events."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Paste / Import Event Log Lines")
        dlg.geometry("700x480")
        dlg.configure(bg=BG_COLOR)
        dlg.transient(self.root)
        dlg.grab_set()

        pad = tk.Frame(dlg, bg=BG_COLOR, padx=14, pady=12)
        pad.pack(fill="both", expand=True)

        tk.Label(
            pad,
            text="Paste Timestamped Detection Event Log lines below (from screen, clipboard, or chat):",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        txt_frame = tk.Frame(pad, bg=BG_COLOR)
        txt_frame.pack(fill="both", expand=True, pady=(0, 10))

        scroll = tk.Scrollbar(txt_frame)
        scroll.pack(side="right", fill="y")

        txt = tk.Text(txt_frame, font=FONT_MONO, bg=BG_COLOR, fg=FG_COLOR, bd=1, relief="solid", yscrollcommand=scroll.set)
        txt.pack(side="left", fill="both", expand=True)
        scroll.config(command=txt.yview)

        def do_import():
            raw_text = txt.get("1.0", tk.END).strip()
            if not raw_text:
                dlg.destroy()
                return

            events = parse_log_text_lines(raw_text)
            if not events:
                messagebox.showwarning("Import Warning", "No valid event patterns found in the pasted text.", parent=dlg)
                return

            for ev in events:
                self.live_events.append(ev)
                self.live_log_entries.append({"type": "HIT", "event": ev})
                save_live_ground_truth_event(ev)

                time_str = time.strftime("%H:%M:%S", time.localtime(ev.wall_clock_time))
                if ev.user_feedback == "TP":
                    status_tag = "✅ TRUE POSITIVE"
                elif ev.user_feedback == "FP":
                    status_tag = "❌ FALSE POSITIVE"
                elif ev.user_feedback == "FN":
                    status_tag = "⚠️ FALSE NEGATIVE"
                else:
                    status_tag = "[PENDING REVIEW]"

                entry_line = (
                    f"[{time_str}] HIT | Conf: {ev.confidence:.0%} | Peak: {ev.peak_amplitude:.3f} | "
                    f"Hz: {ev.spectral_centroid_hz:.0f} | Hi/Lo: {ev.high_low_ratio:.2f} | Status: {status_tag}"
                )
                self.event_log_list.insert(tk.END, entry_line)

            self.evaluation_metrics.true_positives = sum(1 for e in self.live_events if e.user_feedback == "TP")
            self.evaluation_metrics.false_positives = sum(1 for e in self.live_events if e.user_feedback == "FP")
            self.evaluation_metrics.false_negatives = sum(1 for e in self.live_events if e.user_feedback == "FN")
            self._update_live_kpis()
            self._update_live_gt_summary()

            messagebox.showinfo(
                "Import Complete",
                f"Successfully imported {len(events)} events!\nSaved to data/live_ground_truth.csv.",
                parent=dlg,
            )
            dlg.destroy()

        btn_row = tk.Frame(pad, bg=BG_COLOR)
        btn_row.pack(fill="x")

        tk.Button(
            btn_row,
            text="📥 Import Events into Session",
            font=FONT_HEADING,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=2,
            relief="solid",
            command=do_import,
            padx=14,
            pady=4,
        ).pack(side="left")

        tk.Button(
            btn_row,
            text="Cancel",
            font=FONT_BODY,
            bg=BG_COLOR,
            fg=FG_COLOR,
            bd=1,
            relief="solid",
            command=dlg.destroy,
            padx=12,
            pady=4,
        ).pack(side="right")

    def _update_live_kpis(self):
        m = self.evaluation_metrics
        self.live_kpi_lbl.config(
            text=f"TP: {m.true_positives} | FP: {m.false_positives} | FN: {m.false_negatives} | "
            f"Precision: {m.precision:.1%} | Recall: {m.recall:.1%}"
        )
        self._update_metrics_tab()

    def _toggle_guided_alternating_test(self):
        if self.is_alternating_test:
            self.is_alternating_test = False
            self.btn_guided_test.config(text="Guided Alternating Test (Tap vs Type)", bg=BG_COLOR, fg=FG_COLOR)
            self.status_lbl.config(text="Exited guided alternating test.")
        else:
            if not self.is_live_detecting:
                self._toggle_live_detection()
            self.is_alternating_test = True
            self.alternating_step = 1
            self.btn_guided_test.config(text="Stop Guided Test", bg=HIGHLIGHT_BG, fg=HIGHLIGHT_FG)
            self._advance_guided_test()

    def _advance_guided_test(self):
        if not self.is_alternating_test:
            return
        if self.alternating_step % 2 == 1:
            # Tap phase
            self.status_lbl.config(text=f"GUIDED TEST STEP {self.alternating_step}: Please TAP palm-rest 5 times now...")
            self.root.after(8000, self._guided_step_transition)
        else:
            # Typing phase
            self.status_lbl.config(text=f"GUIDED TEST STEP {self.alternating_step}: Please TYPE continuously on keyboard for 10s...")
            self.root.after(10000, self._guided_step_transition)

    def _guided_step_transition(self):
        if not self.is_alternating_test:
            return
        self.alternating_step += 1
        if self.alternating_step > 6:
            self.is_alternating_test = False
            self.btn_guided_test.config(text="Guided Alternating Test (Tap vs Type)", bg=BG_COLOR, fg=FG_COLOR)
            self.status_lbl.config(text="Guided test completed! Review Metrics tab.")
            messagebox.showinfo("Guided Test Finished", "Guided Tap vs Type test finished! Review evaluation metrics.")
        else:
            self._advance_guided_test()

    # -------------------------------------------------------------------------
    # Evaluation Metrics & Report Generation
    # -------------------------------------------------------------------------

    def _update_metrics_tab(self):
        m = self.evaluation_metrics
        if self.live_session_start_time > 0:
            m.test_duration_sec = time.time() - self.live_session_start_time
            m.negative_duration_sec = m.test_duration_sec * 0.7  # approximate

        self.kpi_labels["precision"].config(text=f"{m.precision:.1%}")
        self.kpi_labels["recall"].config(text=f"{m.recall:.1%}")
        self.kpi_labels["f1"].config(text=f"{m.f1_score:.3f}")
        self.kpi_labels["fp_min"].config(text=f"{m.false_positives_per_minute:.2f}")
        self.kpi_labels["latency"].config(text=f"{m.average_latency_ms:.1f} ms")

        # Update text view with confusion matrix
        report_preview = (
            f"{m.confusion_matrix_ascii()}\n\n"
            f"Evaluation Summary:\n"
            f"• True Positives (Deliberate Taps Captured): {m.true_positives}\n"
            f"• False Positives (Typing/Clicks Falsely Triggered): {m.false_positives}\n"
            f"• False Negatives (Intended Taps Missed): {m.false_negatives}\n"
            f"• Precision: {m.precision:.1%}\n"
            f"• Recall: {m.recall:.1%}\n"
            f"• F1 Score: {m.f1_score:.3f}\n"
            f"• False Alarms / Minute: {m.false_positives_per_minute:.2f}\n"
            f"• Total Evaluation Duration: {m.test_duration_sec:.1f} seconds\n"
        )
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert(tk.END, report_preview)

    def _export_evaluation_report(self):
        meta = {
            "device_name": self.device_combo.get(),
            "sample_rate": self.audio_engine.actual_sample_rate,
            "host_api": "Windows Sound Architecture",
        }
        report_path = Path("reports") / "hit_evaluation_report.md"
        generate_markdown_report(self.evaluation_metrics, meta, output_path=report_path)
        messagebox.showinfo(
            "Report Exported",
            f"Scientific evaluation report successfully exported to:\n{report_path.resolve()}",
        )

    def _delete_all_collected_data(self):
        confirm = messagebox.askyesno(
            "Delete All Data",
            "Are you sure you want to delete all calibration recordings, candidate clips, and labels?",
        )
        if confirm:
            for p in [Path("data") / "raw", Path("data") / "events"]:
                if p.exists():
                    shutil.rmtree(p)
                    p.mkdir(parents=True, exist_ok=True)
            for f in [CALIBRATION_FILE, LABELS_CSV_FILE]:
                if f.exists():
                    f.unlink()

            self.ambient_audio = None
            self.tap_candidates = []
            self.negative_candidates = []
            self.live_events = []
            self.live_log_entries = []
            self.evaluation_metrics = EvaluationMetrics()
            self._refresh_candidate_list()
            self.event_log_list.delete(0, tk.END)
            self._update_live_kpis()
            self.lbl_cal_result.config(text="All data deleted. Calibration reset.", fg=MUTED_FG)
            messagebox.showinfo("Reset Complete", "All audio recordings, event clips, and labels have been erased.")

    # -------------------------------------------------------------------------
    # Main UI Poll Loop (Meters, Waveforms, Background Thread Events)
    # -------------------------------------------------------------------------

    def _ui_poll_loop(self):
        # Process items in UI queue
        while not self.ui_update_queue.empty():
            try:
                msg_type, payload = self.ui_update_queue.get_nowait()
                if msg_type == "PROG_A":
                    el, tot = payload
                    self.cal_progress["value"] = (el / tot) * 100
                    self.cal_prog_lbl.config(text=f"Phase A: {el:.1f}s / {tot:.1f}s (Quiet ambient...)")
                    if hasattr(self, "cal_canvas"):
                        self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
                elif msg_type == "DONE_A":
                    self._process_phase_a_result(payload)
                elif msg_type == "PROG_B":
                    el, tot = payload
                    self.cal_progress["value"] = (el / tot) * 100
                    self.cal_prog_lbl.config(text=f"Phase B: {el:.1f}s / {tot:.1f}s (~30 Taps with pauses...)")
                    if hasattr(self, "cal_canvas"):
                        self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
                elif msg_type == "DONE_B":
                    self._process_phase_b_result(payload)
                elif msg_type == "PROG_C":
                    el, tot = payload
                    self.cal_progress["value"] = (el / tot) * 100
                    self.cal_prog_lbl.config(text=f"Phase C: {el:.1f}s / {tot:.1f}s (Type & click...)")
                    if hasattr(self, "cal_canvas"):
                        self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
                elif msg_type == "DONE_C":
                    self._process_phase_c_result(payload)
                elif msg_type in ("HIT", "KNOCK"):
                    self._on_hit_event(payload)
                elif msg_type == "ACTION":
                    gesture, action = payload
                    self._handle_action_fired_ui(gesture, action)
            except queue.Empty:
                break

        # Update VU Meter and Waveform if audio stream running
        if self.audio_engine.is_running:
            pk = self.audio_engine.latest_peak
            # Draw VU meter bar
            self.vu_canvas.delete("all")
            w = self.vu_canvas.winfo_width()
            h = self.vu_canvas.winfo_height()
            if w > 10:
                bar_w = int(w * min(1.0, pk * 2.5))  # scale for visibility
                self.vu_canvas.create_rectangle(0, 0, bar_w, h, fill=HIGHLIGHT_BG, outline="")
                # Peak threshold line indicator
                floor = self.detector.config.amplitude_floor
                line_x = int(w * min(1.0, floor * 2.5))
                self.vu_canvas.create_line(line_x, 0, line_x, h, fill="#888888", width=2)

            # Draw Dual Monitors on all active views
            self._render_dual_monitor(self.waveform_canvas, self.vu_text_lbl)
            if hasattr(self, "cal_canvas"):
                self._render_dual_monitor(self.cal_canvas, self.cal_freq_lbl)
            if hasattr(self, "live_canvas"):
                self._render_dual_monitor(self.live_canvas, self.live_freq_lbl)

            if self.is_live_detecting and self.live_session_start_time > 0:
                elapsed = time.time() - self.live_session_start_time
                self.live_session_info.config(
                    text=f"Session Duration: {elapsed:.1f}s | Events: {len(self.live_events)}"
                )

        # Schedule next loop
        self.root.after(40, self._ui_poll_loop)

    def _render_dual_monitor(self, canvas: tk.Canvas, status_lbl: Optional[tk.Label] = None):
        """Render both Amplitude Waveform and Frequency Centroid line with Cutoff marker."""
        hist = list(self.audio_engine.level_history)
        freq_hist = list(self.audio_engine.centroid_history)
        if not hist:
            return

        try:
            if not canvas.winfo_exists():
                return
            w = canvas.winfo_width()
            h = canvas.winfo_height()
        except Exception:
            return

        if w < 20 or h < 30:
            return

        canvas.delete("all")

        # Top 42%: Amplitude Waveform
        # Bottom 58%: Frequency Centroid Line with 2200 Hz Cutoff Line
        amp_h = int(h * 0.40)
        div_y = amp_h + 6
        freq_top_y = div_y + 12
        freq_bot_y = h - 6
        freq_h = max(20, freq_bot_y - freq_top_y)

        # 1. Amplitude Waveform
        mid_amp_y = amp_h / 2.0
        canvas.create_line(0, mid_amp_y, w, mid_amp_y, fill=LIGHT_BORDER, dash=(2, 2))

        floor = self.detector.config.amplitude_floor
        thr_y = mid_amp_y - (floor * mid_amp_y * 1.8)
        if thr_y > 2:
            canvas.create_line(0, thr_y, w, thr_y, fill="#888888", dash=(4, 4))
            canvas.create_text(w - 6, max(6, thr_y - 2), text=f"Floor ({floor:.3f})", anchor="se", font=("Segoe UI", 7), fill="#666666")

        n_pts = len(hist)
        dx = w / max(1, n_pts - 1)
        amp_coords = []
        for i, val in enumerate(hist):
            x = i * dx
            scaled = min(1.0, val * 2.2) * (mid_amp_y * 0.9)
            amp_coords.extend([x, mid_amp_y - scaled])

        if len(amp_coords) >= 4:
            canvas.create_line(*amp_coords, fill=BORDER_COLOR, width=1.5)

        # 2. Divider Line with Zone Labels
        canvas.create_line(0, div_y, w, div_y, fill=LIGHT_BORDER, width=1)
        canvas.create_text(8, div_y - 1, text="AMPLITUDE WAVEFORM", anchor="sw", font=("Segoe UI", 7, "bold"), fill=FG_COLOR)
        canvas.create_text(8, div_y + 8, text="FREQUENCY SPECTRUM (0 - 5000 Hz)", anchor="nw", font=("Segoe UI", 7, "bold"), fill=FG_COLOR)

        # 3. Frequency Cutoff Line at centroid_max_hz (e.g. 2200 Hz)
        cutoff_hz = self.detector.config.centroid_max_hz
        max_hz = 5000.0
        cutoff_frac = min(1.0, max(0.0, cutoff_hz / max_hz))
        cutoff_y = freq_bot_y - (cutoff_frac * freq_h)

        # Prominent cutoff line
        canvas.create_line(0, cutoff_y, w, cutoff_y, fill="#000000", width=1.5, dash=(6, 3))
        canvas.create_text(
            w - 8,
            cutoff_y - 2,
            text=f"⚡ FREQUENCY CUTOFF LINE ({cutoff_hz:.0f} Hz)",
            anchor="se",
            font=("Segoe UI", 8, "bold"),
            fill="#000000",
        )
        canvas.create_text(8, freq_bot_y - 2, text="[ TAP ZONE: Low chassis thump < 2200 Hz ]", anchor="sw", font=("Segoe UI", 7), fill="#333333")
        canvas.create_text(8, freq_top_y + 12, text="[ TYPING ZONE: High plastic clicks > 2200 Hz ]", anchor="nw", font=("Segoe UI", 7), fill="#777777")

        # 4. Rolling Frequency Centroid Line
        if freq_hist:
            freq_coords = []
            for i, f_val in enumerate(freq_hist):
                x = i * dx
                norm_f = min(1.0, max(0.0, f_val / max_hz))
                y_f = freq_bot_y - (norm_f * freq_h)
                freq_coords.extend([x, y_f])
            if len(freq_coords) >= 4:
                canvas.create_line(*freq_coords, fill="#000000", width=2)

        # 5. Readout Status Text Update
        if status_lbl is not None:
            pk = self.audio_engine.latest_peak
            db = 20.0 * np.log10(pk + 1e-6)
            cent = self.audio_engine.latest_centroid
            if cent > 0:
                zone = "TAP ZONE (Below Line)" if cent <= cutoff_hz else "TYPING ZONE (Above Line)"
                status_lbl.config(text=f"Peak: {pk:.3f} ({db:.1f} dB) | Frequency: {cent:.0f} Hz [{zone}] | Cutoff: {cutoff_hz:.0f} Hz")
            else:
                status_lbl.config(text=f"Peak: {pk:.3f} ({db:.1f} dB) | Frequency: 0 Hz (Idle) | Cutoff: {cutoff_hz:.0f} Hz")

    def _on_close(self):
        self.audio_engine.stop()
        self.root.destroy()


# Backward-compatibility alias
NokAppGUI = TappAppGUI

