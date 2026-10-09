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
    public const float HighLowRatioMax = 0.5470f;
    public const float RefractorySec = 0.1800f;
    public const float MinConfidence = 0.5000f;
    public const float ClassifierBias = 0.8820f;

    public static readonly float[]? ClassifierWeights = new float[] { 1.222322f, 0.527285f, -0.306629f, -0.271932f };
    public static readonly float[]? FeatureMeans = new float[] { 0.255187f, 610.124675f, 2695.162338f, 0.366052f };
    public static readonly float[]? FeatureScales = new float[] { 0.176091f, 821.398291f, 929.714992f, 0.312003f };
}
