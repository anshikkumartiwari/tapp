# Hit Detection Experiment Report

**Date:** 2026-10-10 02:30:32  
**Hardware Target:** Lenovo IdeaPad (Right-side palm-rest area near Lenovo logo)  
**Microphone:** `Test Mic` (Host API: `MME`, Sample Rate: `16000 Hz`)  

---

## 1. Executive Summary

- **Total Intended Taps Evaluated:** 30
- **True Positives (Correctly Detected):** 28
- **False Negatives (Missed Taps):** 2
- **False Positives (Typing/Clicks/Noise Triggered):** 1
- **True Negatives (Correctly Ignored):** 40
- **Precision:** `96.6%`
- **Recall (Sensitivity):** `93.3%`
- **F1 Score:** `0.949`
- **False Alarms per Minute (FP/min):** `2.00`
- **Average Detection Latency:** `25.0 ms`
- **Total Test Duration:** `60.0 s` (Negative testing: `30.0 s`)

---

## 2. Confusion Matrix

```
=====================================================
                 CONFUSION MATRIX                   
=====================================================
                  Actual Tap       Actual Non-Tap   
  Pred Tap:       28     (TP)       1      (FP)   
  Pred Non-Tap:   2      (FN)       40     (TN)   
=====================================================
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
*Report generated automatically by Tapp Hit Test Suite on 2026-10-10 02:30:32*
