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
    public const float HighLowRatioMax = 0.5670f;
    public const float RefractorySec = 0.1800f;
    public const float MinConfidence = 0.5000f;
    public const float ClassifierBias = 1.1171f;

    public static readonly float[]? ClassifierWeights = new float[] { 1.223246f, 0.577201f, -0.168019f, -0.183340f };
    public static readonly float[]? FeatureMeans = new float[] { 0.268561f, 745.152381f, 2738.531746f, 0.336286f };
    public static readonly float[]? FeatureScales = new float[] { 0.177696f, 851.086812f, 818.872178f, 0.294759f };
}
