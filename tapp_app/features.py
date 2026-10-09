"""Feature extraction for transient acoustic hit detection.

Extracts time-domain and frequency-domain characteristics to distinguish
direct chassis mechanical conduction (palm-rest taps) from airborne/plastic
transients (typing, trackpad clicks) and continuous room noise.
"""

from typing import Dict, Optional, Tuple
import numpy as np


CUTOFF_FREQ_HZ = 2400.0  # Boundary between low chassis resonance and high typing click frequencies


def compute_rfft_cached(
    block: np.ndarray,
    sr: int,
    window: Optional[np.ndarray] = None,
    freqs: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Compute magnitude spectrum and corresponding frequencies safely."""
    block = np.asarray(block, dtype=np.float32).reshape(-1)
    n = block.size
    if n == 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

    if window is None:
        window = np.hanning(n).astype(np.float32)
    elif window.size != n:
        window = np.hanning(n).astype(np.float32)

    x = block * window
    mag = np.abs(np.fft.rfft(x))
    if freqs is None or freqs.size != mag.size:
        freqs = np.fft.rfftfreq(n, 1.0 / sr)

    return mag, freqs


def spectral_centroid(
    block: np.ndarray,
    sr: int,
    mag: Optional[np.ndarray] = None,
    freqs: Optional[np.ndarray] = None,
) -> float:
    """Center of mass of the frequency spectrum in Hz.
    
    Chassis thumps typically have low centroids (< 1800-2200 Hz).
    Key-presses and trackpad snaps have high centroids (> 2500-5000+ Hz).
    """
    if mag is None or freqs is None:
        mag, freqs = compute_rfft_cached(block, sr, freqs=freqs)

    sum_mag = float(np.sum(mag))
    if sum_mag <= 1e-9:
        return 0.0
    return float(np.dot(freqs, mag) / sum_mag)


def high_low_energy_ratio(
    block: np.ndarray,
    sr: int,
    cutoff_hz: float = CUTOFF_FREQ_HZ,
    mag: Optional[np.ndarray] = None,
    freqs: Optional[np.ndarray] = None,
) -> float:
    """Ratio of high-frequency energy (>= cutoff) to low-frequency energy (< cutoff).
    
    A mechanical chassis impact on the palm rest transmits heavy low-frequency 
    structural vibrations directly into the laptop chassis and microphone.
    Typing keys creates plastic airborne clicks concentrated in higher frequencies.
    """
    if mag is None or freqs is None:
        mag, freqs = compute_rfft_cached(block, sr, freqs=freqs)

    power = mag ** 2
    low_mask = freqs < cutoff_hz
    high_mask = ~low_mask

    low_power = float(np.sum(power[low_mask]))
    high_power = float(np.sum(power[high_mask]))

    if low_power <= 1e-12:
        return float(high_power / 1e-12)
    return float(high_power / low_power)


def spectral_rolloff(
    block: np.ndarray,
    sr: int,
    percentile: float = 0.85,
    mag: Optional[np.ndarray] = None,
    freqs: Optional[np.ndarray] = None,
) -> float:
    """Frequency below which specified fraction (e.g. 85%) of total spectral energy lies."""
    if mag is None or freqs is None:
        mag, freqs = compute_rfft_cached(block, sr, freqs=freqs)

    power = mag ** 2
    total_power = float(np.sum(power))
    if total_power <= 1e-12:
        return 0.0

    cumsum = np.cumsum(power)
    threshold = percentile * total_power
    idx = np.searchsorted(cumsum, threshold)
    idx = min(idx, freqs.size - 1)
    return float(freqs[idx])


def crest_factor(block: np.ndarray) -> float:
    """Peak-to-RMS ratio indicating transient spike prominence."""
    block = np.asarray(block, dtype=np.float32).reshape(-1)
    if block.size == 0:
        return 0.0
    pk = float(np.max(np.abs(block)))
    rms = float(np.sqrt(np.mean(block ** 2)))
    if rms <= 1e-9:
        return 0.0
    return float(pk / rms)


def extract_features(
    block: np.ndarray,
    sr: int,
    trailing_ema_energy: float = 0.0,
    window: Optional[np.ndarray] = None,
    freqs: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """Extract full feature dictionary for an audio block or candidate event."""
    block = np.asarray(block, dtype=np.float32).reshape(-1)
    if block.size == 0:
        return {
            "peak": 0.0,
            "rms": 0.0,
            "energy": 0.0,
            "attack_ratio": 0.0,
            "crest_factor": 0.0,
            "spectral_centroid": 0.0,
            "high_low_ratio": 0.0,
            "spectral_rolloff": 0.0,
        }

    pk = float(np.max(np.abs(block)))
    energy = float(np.mean(block ** 2))
    rms = float(np.sqrt(energy))

    # Attack ratio: ratio of instantaneous block energy to trailing background EMA
    attack_ratio = float(energy / (trailing_ema_energy + 1e-9)) if trailing_ema_energy > 0 else 1.0

    mag, f_axis = compute_rfft_cached(block, sr, window=window, freqs=freqs)
    sc = spectral_centroid(block, sr, mag=mag, freqs=f_axis)
    hl_ratio = high_low_energy_ratio(block, sr, cutoff_hz=CUTOFF_FREQ_HZ, mag=mag, freqs=f_axis)
    s_rolloff = spectral_rolloff(block, sr, percentile=0.85, mag=mag, freqs=f_axis)
    cf = crest_factor(block)

    return {
        "peak": pk,
        "rms": rms,
        "energy": energy,
        "attack_ratio": attack_ratio,
        "crest_factor": cf,
        "spectral_centroid": sc,
        "high_low_ratio": hl_ratio,
        "spectral_rolloff": s_rolloff,
    }
