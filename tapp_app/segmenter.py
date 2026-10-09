"""Audio segmentation module for extracting candidate transient events.

Extracts isolated event clips from continuous recordings with pre/post context,
debounces ringing impacts, and computes event-level acoustic features.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import scipy.io.wavfile as wavfile

from .features import extract_features


@dataclass
class CandidateEvent:
    event_id: int
    start_sec: float
    peak_sec: float
    end_sec: float
    peak_amplitude: float
    audio: np.ndarray
    features: Dict[str, float]
    label: str = "tap"  # 'tap', 'typing', 'click', 'noise', 'rejected'
    confirmed: bool = False

    def summary(self) -> str:
        sc = self.features.get("spectral_centroid", 0.0)
        hl = self.features.get("high_low_ratio", 0.0)
        ar = self.features.get("attack_ratio", 0.0)
        return (
            f"Event #{self.event_id:02d} @ {self.peak_sec:.2f}s | "
            f"Peak: {self.peak_amplitude:.3f} | Centroid: {sc:.0f}Hz | "
            f"Hi/Lo: {hl:.2f} | Attack: {ar:.1f}x | Label: {self.label}"
        )


def segment_audio_recording(
    audio: np.ndarray,
    sr: int,
    ambient_noise_level: float = 0.01,
    min_peak_threshold: Optional[float] = None,
    refractory_sec: float = 0.15,
    pre_context_sec: float = 0.06,
    post_context_sec: float = 0.14,
    default_label: str = "tap",
    top_k: Optional[int] = None,
) -> List[CandidateEvent]:
    """Segment a continuous audio recording into candidate transient events.
    
    Parameters:
    -----------
    audio : 1D float32 audio array
    sr : sample rate (Hz)
    ambient_noise_level : background noise ceiling estimate
    min_peak_threshold : minimum peak amplitude required (defaults to max(0.02, 2.5 * ambient))
    refractory_sec : minimum duration between consecutive candidate onsets
    pre_context_sec : context window before the detected onset peak
    post_context_sec : context window after the detected onset peak
    default_label : initial candidate label ('tap', 'typing', etc.)
    top_k : if specified, selects the top_k strongest candidates by peak amplitude
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return []

    if min_peak_threshold is None:
        min_peak_threshold = max(0.015, ambient_noise_level * 2.5)

    # Frame-based peak envelope computation
    hop_size = int(sr * 0.005)  # 5ms hop
    win_size = int(sr * 0.015)  # 15ms window
    if hop_size <= 0 or win_size <= 0:
        return []

    n_frames = (audio.size - win_size) // hop_size
    if n_frames <= 0:
        return []

    frame_peaks = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        st = i * hop_size
        frame_peaks[i] = np.max(np.abs(audio[st:st + win_size]))

    # Find peaks above threshold with refractory suppression
    refractory_frames = max(1, int(refractory_sec / 0.005))
    candidate_indices = []

    i = 1
    while i < n_frames - 1:
        pk = frame_peaks[i]
        # Check if local peak above threshold
        if pk >= min_peak_threshold and pk > frame_peaks[i - 1] and pk >= frame_peaks[i + 1]:
            # Look ahead within refractory window to find the global maximum of this cluster
            window_end = min(n_frames, i + refractory_frames)
            best_local_idx = i + int(np.argmax(frame_peaks[i:window_end]))
            candidate_indices.append(best_local_idx)
            # Advance past refractory window
            i = best_local_idx + refractory_frames
        else:
            i += 1

    pre_samples = int(round(pre_context_sec * sr))
    post_samples = int(round(post_context_sec * sr))
    clip_len = pre_samples + post_samples

    candidates: List[CandidateEvent] = []
    event_counter = 1

    for frame_idx in candidate_indices:
        # Approximate peak sample index in the original audio
        peak_sample = frame_idx * hop_size + (win_size // 2)
        start_sample = max(0, peak_sample - pre_samples)
        end_sample = min(audio.size, peak_sample + post_samples)

        clip = audio[start_sample:end_sample]
        # Zero-pad if near start or end
        if clip.size < clip_len:
            pad_before = max(0, pre_samples - peak_sample)
            pad_after = clip_len - clip.size - pad_before
            clip = np.pad(clip, (pad_before, pad_after), mode="constant")

        # Compute trailing ambient energy right before this event
        pre_ambient_start = max(0, start_sample - int(sr * 0.20))
        if pre_ambient_start < start_sample:
            trailing_ema = float(np.mean(audio[pre_ambient_start:start_sample] ** 2))
        else:
            trailing_ema = float(ambient_noise_level ** 2)

        feat = extract_features(clip, sr, trailing_ema_energy=trailing_ema)
        event_peak = float(np.max(np.abs(clip)))

        cand = CandidateEvent(
            event_id=event_counter,
            start_sec=start_sample / sr,
            peak_sec=peak_sample / sr,
            end_sec=end_sample / sr,
            peak_amplitude=event_peak,
            audio=clip,
            features=feat,
            label=default_label,
            confirmed=False,
        )
        candidates.append(cand)
        event_counter += 1

    # If top_k specified (e.g. 30 deliberate taps), select the top_k strongest candidates
    if top_k is not None and len(candidates) > top_k:
        candidates.sort(key=lambda c: c.peak_amplitude, reverse=True)
        candidates = candidates[:top_k]
        # Re-sort chronologically by start time
        candidates.sort(key=lambda c: c.start_sec)
        # Renumber event IDs cleanly 1..top_k
        for idx, c in enumerate(candidates, start=1):
            c.event_id = idx

    return candidates


def save_candidate_wav(
    candidate: CandidateEvent,
    output_path: Path,
    sr: int,
):
    """Save an event snippet to a 16-bit PCM WAV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Normalize clip to avoid clipping when converting to int16
    audio = np.clip(candidate.audio, -1.0, 1.0)
    int16_audio = (audio * 32767.0).astype(np.int16)
    wavfile.write(str(output_path), sr, int16_audio)


def load_candidate_wav(filepath: Path) -> Tuple[np.ndarray, int]:
    """Read a candidate WAV file back into float32 array in [-1.0, 1.0]."""
    sr, data = wavfile.read(str(filepath))
    if data.dtype == np.int16:
        float_data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        float_data = data.astype(np.float32) / 2147483648.0
    elif data.dtype == np.float32:
        float_data = data
    else:
        float_data = data.astype(np.float32)
    return float_data, sr
