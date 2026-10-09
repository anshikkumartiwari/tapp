"""Automated unit and integration test suite for Tapp Hit Detection.
Tests feature extraction, segmentation, streaming detector, calibration, and metrics.
"""

from pathlib import Path
import unittest
import numpy as np

from tapp_app.features import (
    crest_factor,
    extract_features,
    high_low_energy_ratio,
    spectral_centroid,
    spectral_rolloff,
)
from tapp_app.audio_engine import AudioRingBuffer, list_input_devices
from tapp_app.segmenter import (
    CandidateEvent,
    load_candidate_wav,
    save_candidate_wav,
    segment_audio_recording,
)
from tapp_app.detector import DetectedEvent, DetectorConfig, RealtimeHitDetector
from tapp_app.calibration import (
    fit_logistic_regression,
    load_calibration,
    optimize_calibration,
    save_calibration,
)
from tapp_app.metrics import EvaluationMetrics, generate_markdown_report, safe_div


class TestAudioFeatures(unittest.TestCase):
    def setUp(self):
        self.sr = 16000
        self.t = np.linspace(0, 0.05, int(self.sr * 0.05), endpoint=False)

    def test_low_frequency_tap_vs_high_frequency_click(self):
        # Synthetic low-frequency chassis thump: 500 Hz damped sine wave
        decay = np.exp(-self.t * 80)
        low_thump = (np.sin(2 * np.pi * 500 * self.t) * decay).astype(np.float32)

        # Synthetic high-frequency plastic key click: 4000 Hz damped sine wave
        high_click = (np.sin(2 * np.pi * 4000 * self.t) * decay).astype(np.float32)

        sc_thump = spectral_centroid(low_thump, self.sr)
        sc_click = spectral_centroid(high_click, self.sr)

        self.assertLess(sc_thump, 1000.0)
        self.assertGreater(sc_click, 3000.0)

        hl_thump = high_low_energy_ratio(low_thump, self.sr, cutoff_hz=2400.0)
        hl_click = high_low_energy_ratio(high_click, self.sr, cutoff_hz=2400.0)

        self.assertLess(hl_thump, 0.1)
        self.assertGreater(hl_click, 10.0)

    def test_crest_factor_and_features_dict(self):
        block = np.zeros(512, dtype=np.float32)
        block[10] = 1.0  # single sharp impulse
        feats = extract_features(block, self.sr, trailing_ema_energy=0.001)

        self.assertAlmostEqual(feats["peak"], 1.0, places=4)
        self.assertGreater(feats["crest_factor"], 10.0)
        self.assertGreater(feats["attack_ratio"], 1.0)


class TestRingBuffer(unittest.TestCase):
    def test_write_and_read(self):
        buf = AudioRingBuffer(capacity_seconds=1.0, sample_rate=16000)
        data = np.ones(1000, dtype=np.float32) * 0.5
        buf.write(data)
        self.assertEqual(buf.total_written, 1000)

        read_back = buf.read_last(1000 / 16000)
        self.assertEqual(len(read_back), 1000)
        np.testing.assert_allclose(read_back, 0.5)


class TestSegmentation(unittest.TestCase):
    def test_segment_continuous_taps(self):
        sr = 16000
        total_len = sr * 3  # 3 seconds
        audio = np.random.normal(0, 0.002, total_len).astype(np.float32)

        # Insert 3 sharp taps at 0.5s, 1.2s, 2.0s
        for sec in [0.5, 1.2, 2.0]:
            idx = int(sec * sr)
            t_win = np.linspace(0, 0.04, int(sr * 0.04), endpoint=False)
            tap = (np.sin(2 * np.pi * 600 * t_win) * np.exp(-t_win * 100) * 0.4).astype(np.float32)
            audio[idx:idx + len(tap)] += tap

        candidates = segment_audio_recording(audio, sr=sr, ambient_noise_level=0.005)
        self.assertEqual(len(candidates), 3)

        for c in candidates:
            self.assertGreater(c.peak_amplitude, 0.15)
            self.assertEqual(c.label, "tap")

    def test_segment_top_k_strongest(self):
        sr = 16000
        total_len = sr * 4
        audio = np.random.normal(0, 0.002, total_len).astype(np.float32)

        # 4 taps with different amplitudes: 0.1, 0.4, 0.2, 0.5
        amps = [0.1, 0.4, 0.2, 0.5]
        for idx_tap, sec in enumerate([0.5, 1.2, 2.0, 2.8]):
            idx = int(sec * sr)
            t_win = np.linspace(0, 0.04, int(sr * 0.04), endpoint=False)
            tap = (np.sin(2 * np.pi * 600 * t_win) * np.exp(-t_win * 100) * amps[idx_tap]).astype(np.float32)
            audio[idx:idx + len(tap)] += tap

        # Ask for top 2 strongest
        candidates = segment_audio_recording(audio, sr=sr, ambient_noise_level=0.005, top_k=2)
        self.assertEqual(len(candidates), 2)
        # Should pick the 0.4 and 0.5 amplitude taps
        self.assertAlmostEqual(candidates[0].peak_amplitude, 0.4, delta=0.05)
        self.assertAlmostEqual(candidates[1].peak_amplitude, 0.5, delta=0.05)


class TestDetectorAndCalibration(unittest.TestCase):
    def test_streaming_detection(self):
        sr = 16000
        bs = 512
        cfg = DetectorConfig(
            samplerate=sr,
            blocksize=bs,
            amplitude_floor=0.05,
            attack_ratio_threshold=2.5,
            centroid_max_hz=2200.0,
            high_low_ratio_max=0.35,
            refractory_sec=0.15,
        )
        detector = RealtimeHitDetector(cfg)

        # Feed 10 blocks of quiet background
        for i in range(10):
            silence = np.random.normal(0, 0.003, bs).astype(np.float32)
            res = detector.process_block(silence, current_sec=i * (bs / sr))
            self.assertIsNone(res)

        # Feed deliberate tap block (600 Hz thump)
        t_arr = np.linspace(0, bs / sr, bs, endpoint=False)
        tap_block = (np.sin(2 * np.pi * 600 * t_arr) * np.exp(-t_arr * 80) * 0.35).astype(np.float32)
        hit_event = detector.process_block(tap_block, current_sec=11 * (bs / sr))

        self.assertIsNotNone(hit_event)
        self.assertGreaterEqual(hit_event.confidence, 0.50)

    def test_logistic_regression_fit(self):
        # 10 positive, 10 negative 4-dimensional samples
        X_pos = np.random.normal(loc=[0.3, 8.0, 1000.0, 0.1], scale=0.05, size=(10, 4))
        X_neg = np.random.normal(loc=[0.08, 1.5, 3800.0, 1.2], scale=0.05, size=(10, 4))
        X = np.vstack([X_pos, X_neg])
        y = np.concatenate([np.ones(10), np.zeros(10)])

        w, b, means, scales = fit_logistic_regression(X, y)
        self.assertEqual(len(w), 4)

        # Test prediction on a positive sample
        x_test_norm = (X_pos[0] - np.array(means)) / np.array(scales)
        prob = 1.0 / (1.0 + np.exp(-(np.dot(w, x_test_norm) + b)))
        self.assertGreater(prob, 0.8)


class TestMetrics(unittest.TestCase):
    def test_safe_div_and_confusion_matrix(self):
        self.assertEqual(safe_div(5.0, 0.0, default=0.0), 0.0)
        self.assertEqual(safe_div(10.0, 2.0), 5.0)

        m = EvaluationMetrics(
            true_positives=28,
            false_positives=1,
            false_negatives=2,
            true_negatives=40,
            test_duration_sec=60.0,
            negative_duration_sec=30.0,
            latencies_ms=[24.0, 26.0, 25.0],
        )

        self.assertAlmostEqual(m.precision, 28 / 29, places=3)
        self.assertAlmostEqual(m.recall, 28 / 30, places=3)
        self.assertAlmostEqual(m.false_positives_per_minute, 2.0, places=2)  # 1 / 0.5 min = 2.0
        self.assertAlmostEqual(m.average_latency_ms, 25.0, places=1)

        report = generate_markdown_report(m, {"device_name": "Test Mic", "host_api": "MME", "sample_rate": 16000})
        self.assertIn("Precision", report)
        self.assertIn("CONFUSION MATRIX", report)


class TestGestureActions(unittest.TestCase):
    def test_gesture_recognition_single_and_double_tap(self):
        import time
        from tapp_app.actions import ActionSettings, GestureActionManager

        fired = []
        settings = ActionSettings(
            enabled=True,
            single_tap_action="Volume Up",
            double_tap_action="Volume Down",
            triple_tap_action="Mute / Unmute Volume",
            double_tap_timeout_sec=0.10,  # fast timeout for unit testing
        )
        mgr = GestureActionManager(settings=settings, on_action_fired=lambda g, a: fired.append((g, a)))

        # 1. Single tap test: tap once and wait for timeout
        mgr.on_hit_detected()
        time.sleep(0.18)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], ("Single Tap", "Volume Up"))

        # 2. Double tap test: tap twice quickly
        fired.clear()
        mgr.on_hit_detected()
        time.sleep(0.04)
        mgr.on_hit_detected()
        time.sleep(0.18)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], ("Double Tap", "Volume Down"))

        # 3. Triple tap test: tap 3 times quickly
        fired.clear()
        mgr.on_hit_detected()
        time.sleep(0.03)
        mgr.on_hit_detected()
        time.sleep(0.03)
        mgr.on_hit_detected()
        time.sleep(0.05)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], ("Triple Tap", "Mute / Unmute Volume"))


class TestLiveGroundTruthAndRecalibration(unittest.TestCase):
    def test_parse_log_text_lines(self):
        from tapp_app.calibration import parse_log_text_lines

        sample_log = (
            "[17:10:15] HIT | Conf: 92% | Peak: 0.145 | Hz: 1450 | Hi/Lo: 0.22 | Status: ✅ TRUE POSITIVE\n"
            "[17:10:18] HIT | Conf: 65% | Peak: 0.082 | Hz: 3100 | Hi/Lo: 0.52 | Status: ❌ FALSE POSITIVE\n"
            "[17:10:20] HIT | Conf: 88% | Peak: 0.110 | Hz: 1800 | Hi/Lo: 0.28 | Status: [PENDING REVIEW]\n"
        )
        parsed = parse_log_text_lines(sample_log)
        self.assertEqual(len(parsed), 3)

        self.assertAlmostEqual(parsed[0].peak_amplitude, 0.145)
        self.assertAlmostEqual(parsed[0].spectral_centroid_hz, 1450.0)
        self.assertEqual(parsed[0].user_feedback, "TP")

        self.assertAlmostEqual(parsed[1].peak_amplitude, 0.082)
        self.assertAlmostEqual(parsed[1].spectral_centroid_hz, 3100.0)
        self.assertEqual(parsed[1].user_feedback, "FP")

        self.assertIsNone(parsed[2].user_feedback)

    def test_save_load_and_recalibrate(self):
        from tapp_app.calibration import (
            parse_log_text_lines,
            save_live_ground_truth_event,
            load_live_ground_truth_events,
            recalibrate_with_live_ground_truth,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_csv = Path(tmpdir) / "test_gt.csv"
            sample_text = (
                "[17:00:01] HIT | Conf: 95% | Peak: 0.350 | Hz: 1200 | Hi/Lo: 0.15 | Status: ✅ TRUE POSITIVE\n"
                "[17:00:05] HIT | Conf: 60% | Peak: 0.040 | Hz: 3800 | Hi/Lo: 0.85 | Status: ❌ FALSE POSITIVE\n"
            )
            events = parse_log_text_lines(sample_text)
            for ev in events:
                save_live_ground_truth_event(ev, filepath=tmp_csv)

            loaded = load_live_ground_truth_events(filepath=tmp_csv)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].user_feedback, "TP")
            self.assertEqual(loaded[1].user_feedback, "FP")

            # Test recalibration
            amb = np.random.normal(0, 0.002, 16000 * 2).astype(np.float32)
            cfg, summary = recalibrate_with_live_ground_truth(loaded, ambient_audio=amb, sr=16000)
            self.assertGreater(cfg.amplitude_floor, 0.0)
            self.assertEqual(summary["live_taps_added"], 1)
            self.assertEqual(summary["live_negatives_added"], 1)


if __name__ == "__main__":
    unittest.main()
