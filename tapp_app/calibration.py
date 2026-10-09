"""Guided calibration logic and threshold optimizer.

Processes calibration recordings from Phase A (Ambient), Phase B (Taps),
and Phase C (Typing/Clicks), optimizes detection thresholds, trains a
lightweight classifier, and persists calibration metadata to disk.
"""

from dataclasses import asdict
import datetime
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import scipy.optimize

import re
import time
from .detector import DetectedEvent, DetectorConfig
from .segmenter import CandidateEvent


CALIBRATION_FILE = Path("data") / "calibration.json"
LABELS_CSV_FILE = Path("data") / "labels.csv"
LIVE_GROUND_TRUTH_FILE = Path("data") / "live_ground_truth.csv"


def fit_logistic_regression(
    X: np.ndarray,
    y: np.ndarray,
    reg_lambda: float = 0.1,
) -> Tuple[List[float], float, List[float], List[float]]:
    """Fit an L2-regularized logistic regression classifier using scipy.optimize.
    
    Returns (weights, bias, feature_means, feature_scales).
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1)

    means = np.mean(X, axis=0)
    scales = np.std(X, axis=0)
    scales[scales < 1e-6] = 1.0  # prevent division by zero

    X_norm = (X - means) / scales
    n_samples, n_features = X_norm.shape

    # Loss function with L2 penalty
    def loss_and_grad(params):
        w = params[:n_features]
        b = params[n_features]

        z = np.clip(np.dot(X_norm, w) + b, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-z))
        eps = 1e-12

        # Cross entropy loss + L2 regularization on weights (not bias)
        loss = -np.mean(y * np.log(p + eps) + (1.0 - y) * np.log(1.0 - p + eps)) + 0.5 * reg_lambda * np.sum(w ** 2)

        # Gradient
        err = p - y
        grad_w = np.dot(X_norm.T, err) / n_samples + reg_lambda * w
        grad_b = np.mean(err)
        grad = np.concatenate([grad_w, [grad_b]])
        return loss, grad

    initial_params = np.zeros(n_features + 1, dtype=np.float64)
    res = scipy.optimize.minimize(
        loss_and_grad,
        initial_params,
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 200},
    )

    w_opt = res.x[:n_features].tolist()
    b_opt = float(res.x[n_features])
    return w_opt, b_opt, means.tolist(), scales.tolist()


def optimize_calibration(
    ambient_audio: np.ndarray,
    tap_candidates: List[CandidateEvent],
    negative_candidates: List[CandidateEvent],
    sr: int = 16000,
    blocksize: int = 512,
) -> Tuple[DetectorConfig, Dict]:
    """Derive optimal detection thresholds and fit classifier from calibration phases.
    
    Returns (fitted_DetectorConfig, summary_metrics).
    """
    # 1. Ambient noise characterization (quiet room or fan noise)
    amb = np.asarray(ambient_audio, dtype=np.float32)
    n_blocks = amb.size // blocksize
    if n_blocks > 0:
        amb_peaks = np.array([np.max(np.abs(amb[i * blocksize:(i + 1) * blocksize])) for i in range(n_blocks)])
        ambient_ceiling = float(np.percentile(amb_peaks, 99.5))
        ambient_rms = float(np.sqrt(np.mean(amb ** 2)))
    else:
        ambient_ceiling = 0.01
        ambient_rms = 0.005

    # Filter confirmed or valid candidates
    valid_taps = [c for c in tap_candidates if c.label == "tap"]
    valid_negs = [c for c in negative_candidates if c.label in ("typing", "click", "noise")]

    if not valid_taps:
        raise ValueError("Cannot calibrate: No valid tap candidates provided.")

    tap_peaks = np.array([c.peak_amplitude for c in valid_taps])
    tap_attacks = np.array([c.features.get("attack_ratio", 2.0) for c in valid_taps])
    tap_centroids = np.array([c.features.get("spectral_centroid", 1200.0) for c in valid_taps])
    tap_hl_ratios = np.array([c.features.get("high_low_ratio", 0.1) for c in valid_taps])

    weakest_tap = float(np.min(tap_peaks))
    tap_p10 = float(np.percentile(tap_peaks, 10))

    # 2. Optimal Amplitude Floor (Rejects fan noise & incidental typing thumps)
    if valid_negs:
        neg_peaks = np.array([c.peak_amplitude for c in valid_negs])
        neg_thump_ceiling = float(np.percentile(neg_peaks, 95))
        if tap_p10 > neg_thump_ceiling:
            # Deliberate taps are distinctly louder than incidental typing thumps
            amp_sep = 0.5 * (neg_thump_ceiling + tap_p10)
            amp_floor = max(ambient_ceiling * 2.2, min(amp_sep, weakest_tap * 0.80))
        else:
            amp_floor = max(ambient_ceiling * 2.2, min(0.04, weakest_tap * 0.70))
    else:
        amp_floor = max(ambient_ceiling * 2.2, min(0.04, weakest_tap * 0.70))

    # 3. Optimal Attack Ratio Threshold (Rejects continuous fan noise)
    weakest_attack = float(np.min(tap_attacks))
    attack_ratio_thr = max(2.2, min(weakest_attack * 0.65, 4.5))

    # 4. Optimal Centroid Ceiling and High/Low Ratio (Rejects plastic keyclicks)
    tap_centroid_p90 = float(np.percentile(tap_centroids, 90))
    tap_hl_p90 = float(np.percentile(tap_hl_ratios, 90))

    if valid_negs:
        neg_centroids = np.array([c.features.get("spectral_centroid", 3000.0) for c in valid_negs])
        neg_hl_ratios = np.array([c.features.get("high_low_ratio", 0.8) for c in valid_negs])

        neg_centroid_p10 = float(np.percentile(neg_centroids, 10))
        neg_hl_p10 = float(np.percentile(neg_hl_ratios, 10))

        # Centroid ceiling separating low chassis thump from plastic click
        if neg_centroid_p10 > tap_centroid_p90:
            centroid_ceiling = 0.5 * (tap_centroid_p90 + neg_centroid_p10)
        else:
            centroid_ceiling = tap_centroid_p90 * 1.25

        # High/Low ratio max separating chassis resonance from airborne noise
        if neg_hl_p10 > tap_hl_p90:
            hl_max = 0.5 * (tap_hl_p90 + neg_hl_p10)
        else:
            hl_max = max(0.25, tap_hl_p90 * 1.30)
    else:
        centroid_ceiling = tap_centroid_p90 * 1.25
        hl_max = max(0.30, tap_hl_p90 * 1.35)

    # Bound thresholds to realistic acoustic ranges
    centroid_ceiling = max(1300.0, min(3200.0, centroid_ceiling))
    hl_max = max(0.15, min(0.65, hl_max))

    # 5. Train Classifier if we have both positive and negative examples
    w_opt, b_opt, means, scales = None, 0.0, None, None
    cv_accuracy = 1.0

    if len(valid_taps) >= 5 and len(valid_negs) >= 5:
        X_taps = np.column_stack([tap_peaks, tap_attacks, tap_centroids, tap_hl_ratios])
        y_taps = np.ones(len(valid_taps), dtype=np.float64)

        neg_peaks = np.array([c.peak_amplitude for c in valid_negs])
        neg_attacks = np.array([c.features.get("attack_ratio", 1.0) for c in valid_negs])
        X_negs = np.column_stack([neg_peaks, neg_attacks, neg_centroids, neg_hl_ratios])
        y_negs = np.zeros(len(valid_negs), dtype=np.float64)

        X_all = np.vstack([X_taps, X_negs])
        y_all = np.concatenate([y_taps, y_negs])

        w_opt, b_opt, means, scales = fit_logistic_regression(X_all, y_all)

        # Evaluate train accuracy
        X_norm = (X_all - np.array(means)) / np.array(scales)
        probs = 1.0 / (1.0 + np.exp(-np.clip(np.dot(X_norm, np.array(w_opt)) + b_opt, -30, 30)))
        preds = (probs >= 0.5).astype(np.float64)
        cv_accuracy = float(np.mean(preds == y_all))

    cfg = DetectorConfig(
        samplerate=sr,
        blocksize=blocksize,
        amplitude_floor=round(float(amp_floor), 5),
        attack_ratio_threshold=round(float(attack_ratio_thr), 2),
        centroid_max_hz=round(float(centroid_ceiling), 1),
        high_low_ratio_max=round(float(hl_max), 3),
        refractory_sec=0.18,
        min_confidence=0.50,
        classifier_weights=w_opt,
        classifier_bias=round(float(b_opt), 4) if b_opt is not None else 0.0,
        feature_means=means,
        feature_scales=scales,
    )

    summary = {
        "n_taps": len(valid_taps),
        "n_negatives": len(valid_negs),
        "ambient_noise_ceiling": round(ambient_ceiling, 5),
        "amplitude_floor": cfg.amplitude_floor,
        "attack_ratio_threshold": cfg.attack_ratio_threshold,
        "centroid_max_hz": cfg.centroid_max_hz,
        "high_low_ratio_max": cfg.high_low_ratio_max,
        "classifier_fit": (w_opt is not None),
        "training_accuracy": round(cv_accuracy, 3),
        "timestamp": datetime.datetime.now().isoformat(),
    }

    return cfg, summary


def save_calibration(
    config: DetectorConfig,
    summary: Dict,
    device_info: Dict,
    filepath: Path = CALIBRATION_FILE,
):
    """Save calibration configuration and device metadata to json file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "timestamp": datetime.datetime.now().isoformat(),
        "device": device_info,
        "config": config.to_dict(),
        "summary": summary,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_calibration(filepath: Path = CALIBRATION_FILE) -> Optional[Tuple[DetectorConfig, Dict]]:
    """Load calibration configuration from json file if available."""
    filepath = Path(filepath)
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg_dict = data.get("config") or data.get("thresholds") or data
        cfg = DetectorConfig.from_dict(cfg_dict)
        return cfg, data
    except Exception:
        return None


def export_labels_to_csv(
    events: List[CandidateEvent],
    filepath: Path = LABELS_CSV_FILE,
):
    """Save candidate labels and acoustic features to a clean CSV table."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    import csv

    fieldnames = [
        "event_id",
        "start_sec",
        "peak_sec",
        "end_sec",
        "peak_amplitude",
        "attack_ratio",
        "spectral_centroid_hz",
        "high_low_ratio",
        "spectral_rolloff_hz",
        "crest_factor",
        "label",
        "confirmed",
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow({
                "event_id": ev.event_id,
                "start_sec": round(ev.start_sec, 3),
                "peak_sec": round(ev.peak_sec, 3),
                "end_sec": round(ev.end_sec, 3),
                "peak_amplitude": round(ev.peak_amplitude, 5),
                "attack_ratio": round(ev.features.get("attack_ratio", 0.0), 2),
                "spectral_centroid_hz": round(ev.features.get("spectral_centroid", 0.0), 1),
                "high_low_ratio": round(ev.features.get("high_low_ratio", 0.0), 3),
                "spectral_rolloff_hz": round(ev.features.get("spectral_rolloff", 0.0), 1),
                "crest_factor": round(ev.features.get("crest_factor", 0.0), 2),
                "label": ev.label,
                "confirmed": ev.confirmed,
            })


def load_labels_from_csv(filepath: Path = LABELS_CSV_FILE) -> List[CandidateEvent]:
    """Load previously recorded candidate events and acoustic features from CSV."""
    if not filepath.exists():
        return []
    import csv

    candidates = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                features = {
                    "attack_ratio": float(row.get("attack_ratio", 2.0)),
                    "spectral_centroid": float(row.get("spectral_centroid_hz", 1500.0)),
                    "high_low_ratio": float(row.get("high_low_ratio", 0.3)),
                    "spectral_rolloff": float(row.get("spectral_rolloff_hz", 3000.0)),
                    "crest_factor": float(row.get("crest_factor", 10.0)),
                }
                c = CandidateEvent(
                    event_id=int(row.get("event_id", len(candidates) + 1)),
                    start_sec=float(row.get("start_sec", 0.0)),
                    peak_sec=float(row.get("peak_sec", 0.0)),
                    end_sec=float(row.get("end_sec", 0.0)),
                    peak_amplitude=float(row.get("peak_amplitude", 0.1)),
                    audio=np.zeros(1, dtype=np.float32),
                    features=features,
                    label=row.get("label", "tap"),
                    confirmed=row.get("confirmed", "True").lower() == "true",
                )
                candidates.append(c)
    except Exception:
        pass
    return candidates


def save_live_ground_truth_event(
    event: DetectedEvent,
    filepath: Path = LIVE_GROUND_TRUTH_FILE,
):
    """Append a single live detected event to persistent CSV storage with serial number."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    import csv

    fieldnames = [
        "serial_number",
        "timestamp_str",
        "wall_clock_time",
        "peak_amplitude",
        "attack_ratio",
        "spectral_centroid_hz",
        "high_low_ratio",
        "confidence",
        "user_feedback",
    ]
    file_exists = filepath.exists() and filepath.stat().st_size > 0
    serial = 1
    if file_exists:
        try:
            with open(filepath, "r", encoding="utf-8") as f_c:
                serial = max(1, sum(1 for _ in f_c))
        except Exception:
            serial = 1

    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.wall_clock_time))
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "serial_number": serial,
            "timestamp_str": time_str,
            "wall_clock_time": round(event.wall_clock_time, 3),
            "peak_amplitude": round(event.peak_amplitude, 5),
            "attack_ratio": round(event.attack_ratio, 2),
            "spectral_centroid_hz": round(event.spectral_centroid_hz, 1),
            "high_low_ratio": round(event.high_low_ratio, 3),
            "confidence": round(event.confidence, 4),
            "user_feedback": event.user_feedback or "PENDING",
        })


def update_live_ground_truth_file(
    events: List[DetectedEvent],
    filepath: Path = LIVE_GROUND_TRUTH_FILE,
):
    """Rewrite entire live ground truth CSV with serial numbers and updated user feedback labels."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    import csv

    fieldnames = [
        "serial_number",
        "timestamp_str",
        "wall_clock_time",
        "peak_amplitude",
        "attack_ratio",
        "spectral_centroid_hz",
        "high_low_ratio",
        "confidence",
        "user_feedback",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for idx, ev in enumerate(events, 1):
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.wall_clock_time))
            writer.writerow({
                "serial_number": idx,
                "timestamp_str": time_str,
                "wall_clock_time": round(ev.wall_clock_time, 3),
                "peak_amplitude": round(ev.peak_amplitude, 5),
                "attack_ratio": round(ev.attack_ratio, 2),
                "spectral_centroid_hz": round(ev.spectral_centroid_hz, 1),
                "high_low_ratio": round(ev.high_low_ratio, 3),
                "confidence": round(ev.confidence, 4),
                "user_feedback": ev.user_feedback or "PENDING",
            })


def load_live_ground_truth_events(
    filepath: Path = LIVE_GROUND_TRUTH_FILE,
) -> List[DetectedEvent]:
    """Load previously saved live ground truth events from persistent CSV."""
    if not filepath.exists():
        return []
    import csv

    events = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fb = row.get("user_feedback")
                if fb == "PENDING" or not fb:
                    fb = None
                ev = DetectedEvent(
                    timestamp_sec=0.0,
                    wall_clock_time=float(row.get("wall_clock_time", time.time())),
                    peak_amplitude=float(row.get("peak_amplitude", 0.05)),
                    attack_ratio=float(row.get("attack_ratio", 2.0)),
                    spectral_centroid_hz=float(row.get("spectral_centroid_hz", 1500.0)),
                    high_low_ratio=float(row.get("high_low_ratio", 0.3)),
                    confidence=float(row.get("confidence", 0.8)),
                    decision="HIT",
                    user_feedback=fb,
                )
                events.append(ev)
    except Exception:
        pass
    return events


def recalibrate_with_live_ground_truth(
    live_events: List[DetectedEvent],
    ambient_audio: Optional[np.ndarray] = None,
    baseline_csv: Path = LABELS_CSV_FILE,
    sr: int = 16000,
) -> Tuple[DetectorConfig, Dict]:
    """Fine-tune detector calibration using accumulated live ground-truth events (TPs and FPs)."""
    # 1. Load baseline candidates
    baseline_candidates = load_labels_from_csv(baseline_csv)
    taps = [c for c in baseline_candidates if c.label == "tap"]
    negs = [c for c in baseline_candidates if c.label != "tap"]

    # 2. Add confirmed live events
    live_tp_count = 0
    live_fp_count = 0
    next_id = len(baseline_candidates) + 1

    for ev in live_events:
        if ev.user_feedback == "TP":
            feats = {
                "attack_ratio": ev.attack_ratio,
                "spectral_centroid": ev.spectral_centroid_hz,
                "high_low_ratio": ev.high_low_ratio,
                "crest_factor": 10.0,
                "spectral_rolloff": 3000.0,
            }
            cand = CandidateEvent(
                event_id=next_id,
                start_sec=0.0,
                peak_sec=0.0,
                end_sec=0.15,
                peak_amplitude=ev.peak_amplitude,
                audio=np.zeros(1, dtype=np.float32),
                features=feats,
                label="tap",
                confirmed=True,
            )
            taps.append(cand)
            next_id += 1
            live_tp_count += 1
        elif ev.user_feedback == "FP":
            feats = {
                "attack_ratio": ev.attack_ratio,
                "spectral_centroid": ev.spectral_centroid_hz,
                "high_low_ratio": ev.high_low_ratio,
                "crest_factor": 10.0,
                "spectral_rolloff": 3000.0,
            }
            cand = CandidateEvent(
                event_id=next_id,
                start_sec=0.0,
                peak_sec=0.0,
                end_sec=0.15,
                peak_amplitude=ev.peak_amplitude,
                audio=np.zeros(1, dtype=np.float32),
                features=feats,
                label="typing",
                confirmed=True,
            )
            negs.append(cand)
            next_id += 1
            live_fp_count += 1

    if not taps:
        raise ValueError("No tap examples found for calibration.")

    # 3. Load or synthesize ambient audio
    if ambient_audio is None:
        amb_path = Path("data") / "raw" / "ambient.wav"
        if amb_path.exists():
            import scipy.io.wavfile as wavfile
            _, amb_int = wavfile.read(str(amb_path))
            ambient_audio = (amb_int.astype(np.float32) / 32767.0).reshape(-1)
        else:
            ambient_audio = np.random.normal(0, 0.003, sr * 5).astype(np.float32)

    # 4. Run optimization
    cfg, summary = optimize_calibration(
        ambient_audio=ambient_audio,
        tap_candidates=taps,
        negative_candidates=negs,
        sr=sr,
    )

    summary["live_taps_added"] = live_tp_count
    summary["live_negatives_added"] = live_fp_count
    summary["total_taps"] = len(taps)
    summary["total_negatives"] = len(negs)

    # 5. Persist updated configuration
    dev_meta = {"sample_rate": sr}
    old = load_calibration()
    if old:
        _, old_data = old
        dev_meta = old_data.get("device", dev_meta)

    save_calibration(cfg, summary, dev_meta)
    export_labels_to_csv(taps + negs)

    return cfg, summary


def parse_log_text_lines(text: str) -> List[DetectedEvent]:
    """Parse text lines from the event listbox or chat into DetectedEvent objects."""
    parsed = []
    lines = text.strip().splitlines()
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # Extract features using regex
        pk_m = re.search(r"Peak:\s*([\d\.]+)", line_clean)
        hz_m = re.search(r"(?:Hz|Centroid):\s*([\d\.]+)", line_clean)
        hl_m = re.search(r"Hi/Lo:\s*([\d\.]+)", line_clean)
        conf_m = re.search(r"Conf:\s*([\d\.]+)%?", line_clean)
        att_m = re.search(r"Attack:\s*([\d\.]+)x?", line_clean)

        pk = float(pk_m.group(1)) if pk_m else 0.08
        hz = float(hz_m.group(1)) if hz_m else 2000.0
        hl = float(hl_m.group(1)) if hl_m else 0.30
        conf = (float(conf_m.group(1)) / 100.0) if (conf_m and "%" in line_clean) else (float(conf_m.group(1)) if conf_m else 0.75)
        att = float(att_m.group(1)) if att_m else 2.5

        # Extract feedback label
        fb = None
        upper = line_clean.upper()
        if "TRUE POSITIVE" in upper or " TP" in upper:
            fb = "TP"
        elif "FALSE POSITIVE" in upper or "FALSE ALARM" in upper or " FP" in upper:
            fb = "FP"
        elif "FALSE NEGATIVE" in upper or "MISSED" in upper or " FN" in upper:
            fb = "FN"

        ev = DetectedEvent(
            timestamp_sec=0.0,
            wall_clock_time=time.time(),
            peak_amplitude=pk,
            attack_ratio=att,
            spectral_centroid_hz=hz,
            high_low_ratio=hl,
            confidence=conf,
            decision="HIT",
            user_feedback=fb,
        )
        parsed.append(ev)
    return parsed
