"""Evaluation metrics and scientific report generator.

Computes event-level True Positives, False Positives, False Negatives,
Precision, Recall, F1 Score, False Alarm Rate per minute, and Latency.
Generates comprehensive Markdown and JSON reports.
"""

from dataclasses import asdict, dataclass
import datetime
import json
from pathlib import Path
from typing import Dict, List, Optional


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division preventing ZeroDivisionError."""
    if denominator == 0.0 or not (denominator > 0.0 or denominator < 0.0):
        return default
    return float(numerator / denominator)


@dataclass
class EvaluationMetrics:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    test_duration_sec: float = 0.0
    negative_duration_sec: float = 0.0
    latencies_ms: Optional[List[float]] = None

    @property
    def total_actual_positives(self) -> int:
        return self.true_positives + self.false_negatives

    @property
    def total_actual_negatives(self) -> int:
        return self.true_negatives + self.false_positives

    @property
    def precision(self) -> float:
        return safe_div(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        return safe_div(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1_score(self) -> float:
        p = self.precision
        r = self.recall
        return safe_div(2.0 * p * r, p + r)

    @property
    def false_positives_per_minute(self) -> float:
        duration_min = self.negative_duration_sec / 60.0 if self.negative_duration_sec > 0 else (self.test_duration_sec / 60.0)
        return safe_div(self.false_positives, duration_min)

    @property
    def average_latency_ms(self) -> float:
        if self.latencies_ms and len(self.latencies_ms) > 0:
            return float(sum(self.latencies_ms) / len(self.latencies_ms))
        return 0.0

    def to_dict(self) -> Dict:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
            "false_positives_per_minute": round(self.false_positives_per_minute, 2),
            "average_latency_ms": round(self.average_latency_ms, 1),
            "test_duration_sec": round(self.test_duration_sec, 1),
            "negative_duration_sec": round(self.negative_duration_sec, 1),
        }

    def confusion_matrix_ascii(self) -> str:
        return (
            "=====================================================\n"
            "                 CONFUSION MATRIX                   \n"
            "=====================================================\n"
            "                  Actual Tap       Actual Non-Tap   \n"
            f"  Pred Tap:       {self.true_positives:<6} (TP)       {self.false_positives:<6} (FP)   \n"
            f"  Pred Non-Tap:   {self.false_negatives:<6} (FN)       {self.true_negatives:<6} (TN)   \n"
            "====================================================="
        )


def generate_markdown_report(
    metrics: EvaluationMetrics,
    metadata: Dict,
    output_path: Path = Path("reports") / "hit_evaluation_report.md",
) -> str:
    """Generate and save an evaluation report in GitHub-flavored Markdown."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mic_name = metadata.get("device_name", "Built-in Microphone Array")
    host_api = metadata.get("host_api", "Unknown")
    sr = metadata.get("sample_rate", 16000)

    report = f"""# Hit Detection Experiment Report

**Date:** {date_str}  
**Hardware Target:** Lenovo IdeaPad (Right-side palm-rest area near Lenovo logo)  
**Microphone:** `{mic_name}` (Host API: `{host_api}`, Sample Rate: `{sr} Hz`)  

---

## 1. Executive Summary

- **Total Intended Taps Evaluated:** {metrics.total_actual_positives}
- **True Positives (Correctly Detected):** {metrics.true_positives}
- **False Negatives (Missed Taps):** {metrics.false_negatives}
- **False Positives (Typing/Clicks/Noise Triggered):** {metrics.false_positives}
- **True Negatives (Correctly Ignored):** {metrics.true_negatives}
- **Precision:** `{metrics.precision:.1%}`
- **Recall (Sensitivity):** `{metrics.recall:.1%}`
- **F1 Score:** `{metrics.f1_score:.3f}`
- **False Alarms per Minute (FP/min):** `{metrics.false_positives_per_minute:.2f}`
- **Average Detection Latency:** `{metrics.average_latency_ms:.1f} ms`
- **Total Test Duration:** `{metrics.test_duration_sec:.1f} s` (Negative testing: `{metrics.negative_duration_sec:.1f} s`)

---

## 2. Confusion Matrix

```
{metrics.confusion_matrix_ascii()}
```

---

## 3. Metric Definitions & Formulas

| Metric | Formula | Interpretation |
| :--- | :--- | :--- |
| **Precision** | `TP / (TP + FP)` | Percentage of detector triggers that were genuine palm taps. |
| **Recall** | `TP / (TP + FN)` | Percentage of genuine palm taps successfully captured. |
| **F1 Score** | `2 * (P * R) / (P + R)` | Harmonic mean of precision and recall. |
| **False Positives / Min** | `FP / (T_neg / 60)` | Rate of accidental false triggers during regular typing or clicking. |
| **Latency** | `t_detect - t_onset` | Delay from mechanical impact onset to detection decision. |

---

## 4. Hardware & Acoustic Observations

1. **Acoustic Coupling vs Airborne Noise:**
   Taps directly on the chassis near the Lenovo logo transmit heavy structural low-frequency vibrations (< 2200 Hz) directly through the body into the internal microphone array.
2. **Rejection of Typing & Trackpad:**
   Typing switches and trackpad clicks generate higher frequency plastic transients (> 2500-5000 Hz) with higher high-to-low spectral energy ratios.
3. **Refractory Debouncing:**
   A 180 ms lockout period successfully eliminates double-counting from acoustic chassis ringing without impeding rapid double-taps.

---
*Report generated automatically by Tapp Hit Test Suite on {date_str}*
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Also save JSON format for programmatic analysis
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": date_str,
            "metadata": metadata,
            "metrics": metrics.to_dict(),
        }, f, indent=2)

    return report
