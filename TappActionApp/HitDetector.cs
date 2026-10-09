using System;
using System.IO;
using System.Text.Json;

namespace TappActionApp;

/// <summary>
/// Real-time hit detector ported from Python algorithm.
/// Evaluates peak amplitude, attack ratio, spectral centroid, high/low ratio,
/// and L2-regularized logistic regression classification.
/// </summary>
public class HitDetector
{
    public float AmplitudeFloor { get; set; } = CalibratedProfile.AmplitudeFloor;
    public float AttackRatioThreshold { get; set; } = CalibratedProfile.AttackRatioThreshold;
    public float CentroidMaxHz { get; set; } = CalibratedProfile.CentroidMaxHz;
    public float HighLowRatioMax { get; set; } = CalibratedProfile.HighLowRatioMax;
    public float RefractorySec { get; set; } = CalibratedProfile.RefractorySec;
    public float MinConfidence { get; set; } = CalibratedProfile.MinConfidence;
    public float ClassifierBias { get; set; } = CalibratedProfile.ClassifierBias;

    public float[]? ClassifierWeights { get; set; } = CalibratedProfile.ClassifierWeights;
    public float[]? FeatureMeans { get; set; } = CalibratedProfile.FeatureMeans;
    public float[]? FeatureScales { get; set; } = CalibratedProfile.FeatureScales;

    private readonly DspEngine _dsp;
    private float _trailingEma = 0.0f;
    private int _blocksSeen = 0;
    private double _lastEventSec = -1e9;

    public HitDetector(int blockSize = 512, int sampleRate = 16000)
    {
        _dsp = new DspEngine(blockSize, sampleRate);
        LoadCalibration();
    }

    public void LoadCalibration(string? filePath = null)
    {
        // Search in common relative locations
        string[] searchPaths = {
            filePath ?? "",
            "data/calibration.json",
            "../data/calibration.json",
            "../../data/calibration.json",
            "../../../data/calibration.json",
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data", "calibration.json"),
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "..", "..", "data", "calibration.json")
        };

        foreach (var p in searchPaths)
        {
            if (!string.IsNullOrEmpty(p) && File.Exists(p))
            {
                try
                {
                    string json = File.ReadAllText(p);
                    using var doc = JsonDocument.Parse(json);
                    var root = doc.RootElement;
                    if (root.TryGetProperty("config", out var cfg))
                    {
                        if (cfg.TryGetProperty("amplitude_floor", out var af))
                            AmplitudeFloor = (float)af.GetDouble();
                        if (cfg.TryGetProperty("attack_ratio_threshold", out var ar))
                            AttackRatioThreshold = (float)ar.GetDouble();
                        if (cfg.TryGetProperty("centroid_max_hz", out var cm))
                            CentroidMaxHz = (float)cm.GetDouble();
                        if (cfg.TryGetProperty("high_low_ratio_max", out var hl))
                            HighLowRatioMax = (float)hl.GetDouble();
                        if (cfg.TryGetProperty("refractory_sec", out var rf))
                            RefractorySec = (float)rf.GetDouble();
                        if (cfg.TryGetProperty("min_confidence", out var mc))
                            MinConfidence = (float)mc.GetDouble();
                        if (cfg.TryGetProperty("classifier_bias", out var cb))
                            ClassifierBias = (float)cb.GetDouble();

                        if (cfg.TryGetProperty("classifier_weights", out var cw) && cw.ValueKind == JsonValueKind.Array)
                        {
                            ClassifierWeights = new float[cw.GetArrayLength()];
                            int idx = 0;
                            foreach (var w in cw.EnumerateArray()) ClassifierWeights[idx++] = (float)w.GetDouble();
                        }

                        if (cfg.TryGetProperty("feature_means", out var fm) && fm.ValueKind == JsonValueKind.Array)
                        {
                            FeatureMeans = new float[fm.GetArrayLength()];
                            int idx = 0;
                            foreach (var m in fm.EnumerateArray()) FeatureMeans[idx++] = (float)m.GetDouble();
                        }

                        if (cfg.TryGetProperty("feature_scales", out var fs) && fs.ValueKind == JsonValueKind.Array)
                        {
                            FeatureScales = new float[fs.GetArrayLength()];
                            int idx = 0;
                            foreach (var s in fs.EnumerateArray()) FeatureScales[idx++] = (float)s.GetDouble();
                        }
                    }
                    break;
                }
                catch
                {
                    // Fall back to tuned defaults
                }
            }
        }
    }

    public bool ProcessBlock(
        float[] block,
        double currentTimeSec,
        out float confidence,
        out float peak,
        out float centroid,
        out float attackRatio)
    {
        confidence = 0.0f;
        _blocksSeen++;

        _dsp.ExtractFeatures(block, out peak, out float energy, out centroid, out float highLowRatio);

        float prevEma = _trailingEma;
        float alpha = 0.10f;
        _trailingEma = (1.0f - alpha) * prevEma + alpha * energy;

        if (_blocksSeen < 5)
        {
            attackRatio = 1.0f;
            return false;
        }

        attackRatio = energy / (prevEma + 1e-9f);

        // Fast reject: peak below floor
        if (peak < AmplitudeFloor)
        {
            return false;
        }

        // Fast reject: transient attack ratio too low
        if (attackRatio < AttackRatioThreshold)
        {
            return false;
        }

        // Fast reject: refractory period lockout
        if ((currentTimeSec - _lastEventSec) < RefractorySec)
        {
            return false;
        }

        // Calculate confidence score
        confidence = CalculateConfidence(peak, attackRatio, centroid, highLowRatio);

        if (confidence < MinConfidence)
        {
            return false;
        }

        // Detected valid hit!
        _lastEventSec = currentTimeSec;
        return true;
    }

    private float CalculateConfidence(float peak, float attackRatio, float centroid, float highLowRatio)
    {
        if (ClassifierWeights != null && FeatureMeans != null && FeatureScales != null &&
            ClassifierWeights.Length == 4 && FeatureMeans.Length == 4 && FeatureScales.Length == 4)
        {
            float[] raw = { peak, attackRatio, centroid, highLowRatio };
            float z = ClassifierBias;
            for (int i = 0; i < 4; i++)
            {
                float std = (raw[i] - FeatureMeans[i]) / (FeatureScales[i] + 1e-9f);
                z += ClassifierWeights[i] * std;
            }

            z = Math.Clamp(z, -15.0f, 15.0f);
            return 1.0f / (1.0f + MathF.Exp(-z));
        }

        // Rule-based heuristic fallback
        float ampScore = Math.Clamp((peak - AmplitudeFloor) / (AmplitudeFloor * 2.0f + 1e-9f), 0.0f, 1.0f);
        float attScore = Math.Clamp(attackRatio / (AttackRatioThreshold * 2.0f), 0.0f, 1.0f);
        float scScore = (centroid > CentroidMaxHz) ? 0.0f : Math.Clamp(1.0f - (centroid / CentroidMaxHz), 0.0f, 1.0f);
        float hlScore = (highLowRatio > HighLowRatioMax) ? 0.0f : Math.Clamp(1.0f - (highLowRatio / HighLowRatioMax), 0.0f, 1.0f);

        return 0.25f * ampScore + 0.35f * attScore + 0.20f * scScore + 0.20f * hlScore;
    }
}
