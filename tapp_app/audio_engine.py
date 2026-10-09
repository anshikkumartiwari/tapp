"""Audio capture engine using sounddevice.

Provides input device enumeration, device probing, thread-safe circular buffering,
live level metering (peak/RMS), and calibration recording helpers.
"""

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal
import sounddevice as sd


DEFAULT_SAMPLE_RATE = 16000
BLOCK_SIZE = 512


@dataclass
class AudioDeviceInfo:
    index: int
    name: str
    hostapi_name: str
    max_input_channels: int
    default_samplerate: int
    is_default: bool

    def display_name(self) -> str:
        default_tag = " (Default)" if self.is_default else ""
        return (
            f"[{self.index}] {self.name} | {self.hostapi_name} | "
            f"{self.max_input_channels} ch @ {self.default_samplerate}Hz{default_tag}"
        )


def list_input_devices() -> Tuple[List[AudioDeviceInfo], Optional[int]]:
    """Enumerate all available audio input devices on the system."""
    devices = []
    hostapis = sd.query_hostapis()
    
    # Identify system default input device
    default_input_idx = None
    try:
        default_dev = sd.query_devices(kind="input")
        if default_dev is not None:
            default_input_idx = default_dev.get("index")
    except Exception:
        pass

    all_devs = sd.query_devices()
    for idx, dev in enumerate(all_devs):
        channels = dev.get("max_input_channels", 0)
        if channels > 0:
            h_idx = dev.get("hostapi", 0)
            h_name = hostapis[h_idx]["name"] if h_idx < len(hostapis) else "Unknown"
            is_def = (idx == default_input_idx)
            info = AudioDeviceInfo(
                index=idx,
                name=dev.get("name", f"Device {idx}"),
                hostapi_name=h_name,
                max_input_channels=channels,
                default_samplerate=int(dev.get("default_samplerate", DEFAULT_SAMPLE_RATE)),
                is_default=is_def,
            )
            devices.append(info)

    # If default was not found, pick the first device
    if default_input_idx is None and devices:
        default_input_idx = devices[0].index

    return devices, default_input_idx


class AudioRingBuffer:
    """Thread-safe circular audio buffer for float32 mono samples."""

    def __init__(self, capacity_seconds: float = 5.0, sample_rate: int = DEFAULT_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.capacity = max(1024, int(capacity_seconds * sample_rate))
        self._buffer = np.zeros(self.capacity, dtype=np.float32)
        self._write_pos = 0
        self._total_written = 0
        self._lock = threading.Lock()

    def reset(self, sample_rate: Optional[int] = None):
        with self._lock:
            if sample_rate is not None:
                self.sample_rate = sample_rate
            self._buffer.fill(0.0)
            self._write_pos = 0
            self._total_written = 0

    def write(self, samples: np.ndarray):
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        n = samples.size
        if n == 0:
            return

        with self._lock:
            if n >= self.capacity:
                self._buffer[:] = samples[-self.capacity:]
                self._write_pos = 0
            else:
                pos = self._write_pos
                end = pos + n
                if end <= self.capacity:
                    self._buffer[pos:end] = samples
                else:
                    first = self.capacity - pos
                    self._buffer[pos:self.capacity] = samples[:first]
                    self._buffer[0:end - self.capacity] = samples[first:]
                self._write_pos = end % self.capacity
            self._total_written += n

    def read_last(self, seconds: float) -> np.ndarray:
        """Return the most recent `seconds` worth of audio as a 1D float32 array."""
        n_req = int(round(seconds * self.sample_rate))
        if n_req <= 0:
            return np.zeros(0, dtype=np.float32)

        with self._lock:
            avail = min(n_req, self.capacity, self._total_written)
            if avail <= 0:
                return np.zeros(0, dtype=np.float32)

            pos = self._write_pos
            start = (pos - avail) % self.capacity
            if start + avail <= self.capacity:
                return self._buffer[start:start + avail].copy()
            else:
                first = self.capacity - start
                out = np.empty(avail, dtype=np.float32)
                out[:first] = self._buffer[start:self.capacity]
                out[first:] = self._buffer[0:avail - first]
                return out

    @property
    def total_written(self) -> int:
        with self._lock:
            return self._total_written


class AudioCaptureEngine:
    """Manages audio capture stream, device selection, meter levels, and consumer callbacks."""

    def __init__(
        self,
        device_index: Optional[int] = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        block_size: int = BLOCK_SIZE,
    ):
        self.device_index = device_index
        self.requested_sample_rate = sample_rate
        self.actual_sample_rate = sample_rate
        self.block_size = block_size

        self.ring_buffer = AudioRingBuffer(capacity_seconds=6.0, sample_rate=sample_rate)

        # Metering and display state (thread-safe reads)
        self.latest_peak = 0.0
        self.latest_rms = 0.0
        self.latest_centroid = 0.0
        self.latest_dominant_freq = 0.0
        self.latest_mag = np.zeros(0, dtype=np.float32)
        self.level_history = deque(maxlen=200)  # rolling history of peak levels for visual plot
        self.centroid_history = deque(maxlen=200)  # rolling history of spectral centroid (Hz)
        self.is_running = False
        self.error_message: Optional[str] = None

        self._window = np.hanning(self.block_size).astype(np.float32)
        self._freqs = np.fft.rfftfreq(self.block_size, 1.0 / self.actual_sample_rate)

        self._stream: Optional[sd.InputStream] = None
        self._consumer_callbacks: List[Callable[[np.ndarray, float], None]] = []
        self._lock = threading.Lock()

    def add_consumer(self, callback: Callable[[np.ndarray, float], None]):
        """Register a callback receiving (mono_block, current_time_sec) for real-time analysis."""
        with self._lock:
            if callback not in self._consumer_callbacks:
                self._consumer_callbacks.append(callback)

    def remove_consumer(self, callback: Callable[[np.ndarray, float], None]):
        with self._lock:
            if callback in self._consumer_callbacks:
                self._consumer_callbacks.remove(callback)

    def _audio_callback(self, indata, frames, time_info, status):
        """High-performance audio callback from PortAudio."""
        # Downmix to mono
        if indata.shape[1] == 1:
            mono = indata[:, 0].copy()
        else:
            mono = np.mean(indata, axis=1)

        pk = float(np.max(np.abs(mono)))
        rms = float(np.sqrt(np.mean(mono ** 2)))

        self.latest_peak = pk
        self.latest_rms = rms
        self.level_history.append(pk)

        # Real-time spectral and frequency calculation
        if self._window.size != mono.size:
            self._window = np.hanning(mono.size).astype(np.float32)
            self._freqs = np.fft.rfftfreq(mono.size, 1.0 / self.actual_sample_rate)

        mag = np.abs(np.fft.rfft(mono * self._window))
        self.latest_mag = mag
        smag = float(np.sum(mag))

        if pk > 0.003 and smag > 1e-6:
            sc = float(np.dot(self._freqs, mag) / smag)
            if len(mag) > 2:
                peak_bin = int(np.argmax(mag[1:])) + 1
                dom_f = float(self._freqs[peak_bin])
            else:
                dom_f = 0.0
            self.latest_centroid = sc
            self.latest_dominant_freq = dom_f
            self.centroid_history.append(sc)
        elif pk <= 0.003:
            self.latest_centroid = 0.0
            self.latest_dominant_freq = 0.0
            self.centroid_history.append(0.0)

        # Write to circular buffer
        self.ring_buffer.write(mono)

        now_sec = self.ring_buffer.total_written / self.actual_sample_rate

        # Dispatch to consumers
        with self._lock:
            consumers = list(self._consumer_callbacks)
        for cb in consumers:
            try:
                cb(mono, now_sec)
            except Exception:
                pass

    def read_recent_seconds(self, seconds: float) -> np.ndarray:
        """Return the most recent `seconds` worth of audio from circular buffer."""
        return self.ring_buffer.read_last(seconds)

    def get_waveform_snapshot(self, duration_sec: float = 0.70) -> np.ndarray:
        """Return recent audio samples suitable for oscilloscope rendering."""
        return self.ring_buffer.read_last(duration_sec)

    def get_dual_waveform_snapshot(self, duration_sec: float = 0.70) -> Tuple[np.ndarray, np.ndarray]:
        """Return (raw_samples, chassis_lowpass_samples) for the recent duration."""
        raw = self.ring_buffer.read_last(duration_sec)
        if len(raw) < 32:
            return raw, raw
        try:
            sos = scipy.signal.butter(2, 350, btype="low", fs=self.actual_sample_rate, output="sos")
            chassis = scipy.signal.sosfilt(sos, raw)
            return raw, chassis
        except Exception:
            return raw, raw

    def get_spectrum_snapshot(self) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """Return (frequencies, magnitudes, dominant_freq, centroid)."""
        freqs = getattr(self, "_freqs", np.zeros(0, dtype=np.float32))
        mag = getattr(self, "latest_mag", np.zeros(0, dtype=np.float32))
        dom_f = getattr(self, "latest_dominant_freq", 0.0)
        sc = getattr(self, "latest_centroid", 0.0)
        return freqs, mag, dom_f, sc

    def start(self, device_index: Optional[int] = None, sample_rate: Optional[int] = None) -> bool:
        """Start capturing audio on the given device and sample rate."""
        self.stop()

        if device_index is not None:
            self.device_index = device_index
        if sample_rate is not None:
            self.requested_sample_rate = sample_rate

        self.error_message = None

        # Verify device index
        if self.device_index is None:
            _, def_idx = list_input_devices()
            self.device_index = def_idx

        if self.device_index is None:
            self.error_message = "No audio input device available."
            return False

        # Attempt opening at requested sample rate, or fall back to device default
        rates_to_try = [self.requested_sample_rate, 44100, 48000, 16000]
        # Remove duplicates while preserving order
        rates_to_try = list(dict.fromkeys(rates_to_try))

        opened = False
        last_exc = None

        for rate in rates_to_try:
            try:
                self.actual_sample_rate = rate
                self.ring_buffer.reset(sample_rate=rate)
                
                # Check device settings
                sd.check_input_settings(device=self.device_index, samplerate=rate, channels=1)

                self._stream = sd.InputStream(
                    samplerate=rate,
                    blocksize=self.block_size,
                    device=self.device_index,
                    channels=1,
                    dtype="float32",
                    callback=self._audio_callback,
                )
                self._stream.start()
                opened = True
                break
            except Exception as exc:
                last_exc = exc
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None

        if not opened:
            self.error_message = f"Failed to open microphone (Device {self.device_index}): {last_exc}"
            self.is_running = False
            return False

        self.is_running = True
        return True

    def stop(self):
        """Stop the audio capture stream."""
        self.is_running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def record_duration(
        self,
        duration_sec: float,
        progress_callback: Optional[Callable[[float, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Optional[np.ndarray]:
        """Synchronously record for a fixed duration using the active device/sample rate.
        
        Leaves the stream in a clean state and reports progress.
        """
        if self.device_index is None:
            return None

        sr = self.actual_sample_rate
        total_frames = int(round(duration_sec * sr))
        chunk_frames = int(round(0.1 * sr))  # 100ms updates
        
        frames_recorded = 0
        collected_chunks = []

        try:
            # We record via sd.InputStream loop for cancelability and progress updates
            with sd.InputStream(
                samplerate=sr,
                channels=1,
                device=self.device_index,
                dtype="float32",
            ) as stream:
                while frames_recorded < total_frames:
                    if cancel_event and cancel_event.is_set():
                        return None
                    
                    to_read = min(chunk_frames, total_frames - frames_recorded)
                    data, overflowed = stream.read(to_read)
                    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
                    collected_chunks.append(mono)
                    frames_recorded += to_read

                    pk = float(np.max(np.abs(mono)))
                    self.latest_peak = pk
                    self.level_history.append(pk)
                    if pk > 0.005:
                        if self._window.size != mono.size:
                            self._window = np.hanning(mono.size).astype(np.float32)
                            self._freqs = np.fft.rfftfreq(mono.size, 1.0 / sr)
                        mag = np.abs(np.fft.rfft(mono * self._window))
                        smag = float(np.sum(mag))
                        sc = float(np.dot(self._freqs, mag) / (smag + 1e-9)) if smag > 1e-6 else 0.0
                        self.latest_centroid = sc
                        self.centroid_history.append(sc)
                    else:
                        self.latest_centroid = 0.0
                        self.centroid_history.append(0.0)

                    elapsed = frames_recorded / sr
                    if progress_callback:
                        progress_callback(elapsed, duration_sec)

            if collected_chunks:
                full_audio = np.concatenate(collected_chunks, axis=0)
                return full_audio
            return None
        except Exception as exc:
            self.error_message = f"Recording failed: {exc}"
            return None
