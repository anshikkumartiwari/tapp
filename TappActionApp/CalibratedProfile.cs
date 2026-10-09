namespace TappActionApp;

/// <summary>
/// Hardcoded acoustic calibration profile baked directly into the standalone binary.
/// Generated automatically during the guided calibration wizard.
/// </summary>
public static class CalibratedProfile
{
    public const float AmplitudeFloor = 0.0337f;
    public const float AttackRatioThreshold = 2.2000f;
    public const float CentroidMaxHz = 3200.0f;
    public const float HighLowRatioMax = 0.5350f;
    public const float RefractorySec = 0.1800f;
    public const float MinConfidence = 0.5000f;
    public const float ClassifierBias = 0.7647f;

    public static readonly float[]? ClassifierWeights = new float[] { 1.209047f, 0.499027f, -0.370723f, -0.319913f };
    public static readonly float[]? FeatureMeans = new float[] { 0.248269f, 540.282759f, 2672.729885f, 0.381448f };
    public static readonly float[]? FeatureScales = new float[] { 0.174854f, 796.682976f, 981.399371f, 0.319472f };
}
