"""Real-time streaming hit detector.

Processes incoming audio blocks with low latency (< 25ms), updates trailing
background noise estimates, computes temporal and spectral features, applies
refractory guards, and produces timestamped hit events with confidence scores.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import queue
import time
from typing import Dict, List, Optional, Tuple
import numpy as np

from .features import compute_rfft_cached, high_low_energy_ratio, spectral_centroid


@dataclass
class DetectorConfig:
    samplerate: int = 16000
    blocksize: int = 512
    # Detection thresholds (tuned by calibration wizard)
    amplitude_floor: float = 0.035       # minimum peak amplitude above ambient
    attack_ratio_threshold: float = 2.5  # minimum energy jump vs trailing noise EMA
    centroid_max_hz: float = 2200.0      # maximum spectral centroid (chassis thump ceiling)
    high_low_ratio_max: float = 0.35     # max ratio of high (>2.4kHz) to low (<2.4kHz) energy
    refractory_sec: float = 0.18         # lockout period after detection to debounce ringing
    ema_alpha: float = 0.10              # smoothing factor for trailing ambient energy
    warmup_blocks: int = 5               # blocks to ignore while EMA initializes
    min_confidence: float = 0.50         # threshold to emit detection
    # Model parameters for calibrated classifier (weights + bias)
    classifier_weights: Optional[List[float]] = None
    classifier_bias: float = 0.0
    feature_means: Optional[List[float]] = None
    feature_scales: Optional[List[float]] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "DetectorConfig":
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class DetectedEvent:
    timestamp_sec: float
    wall_clock_time: float
    peak_amplitude: float
    attack_ratio: float
    spectral_centroid_hz: float
    high_low_ratio: float
    confidence: float
    decision: str = "HIT"
    user_feedback: Optional[str] = None  # 'TP', 'FP', or None

    def formatted_summary(self) -> str:
        time_str = time.strftime("%H:%M:%S", time.localtime(self.wall_clock_time))
        millis = int((self.wall_clock_time % 1) * 1000)
        return (
            f"[{time_str}.{millis:03d}] HIT DETECTED | Conf: {self.confidence:.0%} | "
            f"Peak: {self.peak_amplitude:.3f} | Centroid: {self.spectral_centroid_hz:.0f}Hz | "
            f"Hi/Lo: {self.high_low_ratio:.2f} | Attack: {self.attack_ratio:.1f}x"
        )


class RealtimeHitDetector:
    """Processes streaming audio blocks from AudioCaptureEngine and triggers hit events."""

    def __init__(self, config: Optional[DetectorConfig] = None):
        self.config = config or DetectorConfig()
        self.event_queue: queue.Queue = queue.Queue(maxsize=1000)

        self._trailing_ema_energy = 0.0
        self._blocks_seen = 0
        self._last_event_sec = -1e9
        self._window: Optional[np.ndarray] = None
        self._freqs: Optional[np.ndarray] = None

        self._init_fft_cache()

    def _init_fft_cache(self):
        bs = self.config.blocksize
        sr = self.config.samplerate
        self._window = np.hanning(bs).astype(np.float32)
        self._freqs = np.fft.rfftfreq(bs, 1.0 / sr)

    def reset(self):
        """Reset internal EMA and timing state."""
        self._trailing_ema_energy = 0.0
        self._blocks_seen = 0
        self._last_event_sec = -1e9
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

    def update_config(self, config: DetectorConfig):
        self.config = config
        self._init_fft_cache()
        self.reset()

    def calculate_confidence(
        self,
        peak: float,
        attack_ratio: float,
        centroid: float,
        high_low_ratio: float,
    ) -> float:
        """Calculate confidence score [0.0, 1.0] using trained linear classifier or rule heuristic."""
        cfg = self.config

        # If trained classifier weights are available, compute logistic sigmoid
        if (
            cfg.classifier_weights is not None
            and cfg.feature_means is not None
            and cfg.feature_scales is not None
        ):
            raw_feats = np.array([peak, attack_ratio, centroid, high_low_ratio], dtype=np.float32)
            means = np.array(cfg.feature_means, dtype=np.float32)
            scales = np.array(cfg.feature_scales, dtype=np.float32)
            weights = np.array(cfg.classifier_weights, dtype=np.float32)
            bias = cfg.classifier_bias

            # Standardize features
            std_feats = (raw_feats - means) / (scales + 1e-9)
            z = float(np.dot(weights, std_feats) + bias)
            # Sigmoid with numerical clip
            z_clipped = max(-15.0, min(15.0, z))
            prob = 1.0 / (1.0 + np.exp(-z_clipped))
            return float(prob)

        # Rule-based calibrated scoring
        # 1. Amplitude score: peak vs floor
        if peak < cfg.amplitude_floor:
            return 0.0
        amp_score = min(1.0, (peak - cfg.amplitude_floor) / (cfg.amplitude_floor * 2.0 + 1e-9))

        # 2. Attack ratio score
        if attack_ratio < cfg.attack_ratio_threshold:
            return 0.0
        attack_score = min(1.0, attack_ratio / (cfg.attack_ratio_threshold * 2.0))

        # 3. Spectral centroid score: reward low frequency thumps, penalize high frequencies
        if centroid > cfg.centroid_max_hz:
            sc_score = 0.0
        else:
            sc_score = max(0.0, 1.0 - (centroid / (cfg.centroid_max_hz + 1e-9)))

        # 4. High/Low energy ratio score: penalize plastic/key click energy
        if high_low_ratio > cfg.high_low_ratio_max:
            hl_score = 0.0
        else:
            hl_score = max(0.0, 1.0 - (high_low_ratio / (cfg.high_low_ratio_max + 1e-9)))

        # Weighted combination
        conf = 0.25 * amp_score + 0.35 * attack_score + 0.20 * sc_score + 0.20 * hl_score
        return float(conf)

    def process_block(self, block: np.ndarray, current_sec: float) -> Optional[DetectedEvent]:
        """Process one incoming audio block in real time."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return None

        cfg = self.config
        self._blocks_seen += 1

        pk = float(np.max(np.abs(block)))
        energy = float(np.mean(block ** 2))

        # Track trailing background noise with exponential moving average
        prev_ema = self._trailing_ema_energy
        alpha = cfg.ema_alpha
        self._trailing_ema_energy = (1.0 - alpha) * prev_ema + alpha * energy

        # Warmup period to let EMA settle
        if self._blocks_seen < cfg.warmup_blocks:
            return None

        # Quick reject: too quiet
        if pk < cfg.amplitude_floor:
            return None

        # Attack ratio transient check
        attack_ratio = float(energy / (prev_ema + 1e-9))
        if attack_ratio < cfg.attack_ratio_threshold:
            return None

        # Refractory guard check: avoid double triggers from chassis acoustic ringing
        if (current_sec - self._last_event_sec) < cfg.refractory_sec:
            return None

        # Extract spectral features
        mag, freqs = compute_rfft_cached(
            block,
            cfg.samplerate,
            window=self._window,
            freqs=self._freqs,
        )
        sc = spectral_centroid(block, cfg.samplerate, mag=mag, freqs=freqs)
        hl_ratio = high_low_energy_ratio(block, cfg.samplerate, mag=mag, freqs=freqs)

        # Centroid ceiling check
        if sc > cfg.centroid_max_hz * 1.25 and cfg.classifier_weights is None:
            return None

        # Confidence calculation
        confidence = self.calculate_confidence(pk, attack_ratio, sc, hl_ratio)
        if confidence < cfg.min_confidence:
            return None

        # Valid hit detected!
        self._last_event_sec = current_sec

        event = DetectedEvent(
            timestamp_sec=current_sec,
            wall_clock_time=time.time(),
            peak_amplitude=pk,
            attack_ratio=attack_ratio,
            spectral_centroid_hz=sc,
            high_low_ratio=hl_ratio,
            confidence=confidence,
            decision="HIT",
        )

        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

        return event


# Backward-compatibility alias
RealtimeKnockDetector = RealtimeHitDetector

