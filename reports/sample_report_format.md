# Sample Hit Detection Evaluation Report Format

**Date:** YYYY-MM-DD HH:MM:SS  
**Hardware Target:** Lenovo IdeaPad (Right-side palm-rest area near Lenovo logo)  
**Microphone:** `Microphone Array (Realtek(R) Audio)` (Host API: `MME`, Sample Rate: `16000 Hz`)  

---

## 1. Executive Summary

- **Total Intended Taps Evaluated:** `<N_TAPS>`
- **True Positives (Correctly Detected):** `<TP>`
- **False Negatives (Missed Taps):** `<FN>`
- **False Positives (Typing/Clicks/Noise Triggered):** `<FP>`
- **True Negatives (Correctly Ignored):** `<TN>`
- **Precision:** `<TP / (TP + FP)>`
- **Recall (Sensitivity):** `<TP / (TP + FN)>`
- **F1 Score:** `<2 * P * R / (P + R)>`
- **False Alarms per Minute (FP/min):** `<FP / (Negative_Duration_Minutes)>`
- **Average Detection Latency:** `<LATENCY_MS> ms`
- **Total Test Duration:** `<DURATION_SEC> s` (Negative testing: `<NEG_DURATION_SEC> s`)

---

## 2. Confusion Matrix

```
=====================================================
                 CONFUSION MATRIX                   
=====================================================
                  Actual Tap       Actual Non-Tap   
  Pred Tap:       <TP>   (TP)      <FP>   (FP)      
  Pred Non-Tap:   <FN>   (FN)      <TN>   (TN)      
=====================================================
```

---

## 3. Metric Definitions & Formulas

| Metric | Formula | Interpretation |
| :--- | :--- | :--- |
| **Precision** | `TP / (TP + FP)` | Proportion of detector alerts that correspond to intended taps. |
| **Recall** | `TP / (TP + FN)` | Proportion of intended taps successfully captured by the detector. |
| **F1 Score** | `2 * (P * R) / (P + R)` | Harmonic mean balancing precision and sensitivity. |
| **False Positives / Min** | `FP / (T_neg / 60)` | Frequency of spurious false triggers during normal laptop typing/clicking. |
| **Detection Latency** | `t_decision - t_onset` | Milliseconds between mechanical impact and detector trigger event. |

---

## 4. Hardware & Physical Observations

1. **Acoustic Coupling vs Airborne Noise:**
   Direct palm-rest taps couple directly into the laptop chassis, generating low-to-mid frequency resonance (< 2200 Hz).
2. **Rejection of Typing & Trackpad:**
   Keystrokes and trackpad clicks generate high-frequency transients (> 2500-5000 Hz) with high spectral energy ratios.
3. **Refractory Debouncing:**
   A 180 ms refractory window eliminates multi-triggers from physical chassis ringing.
