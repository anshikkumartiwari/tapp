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
    public const float HighLowRatioMax = 0.5550f;
    public const float RefractorySec = 0.1800f;
    public const float MinConfidence = 0.5000f;
    public const float ClassifierBias = 0.9701f;

    public static readonly float[]? ClassifierWeights = new float[] { 1.226851f, 0.546893f, -0.256704f, -0.237483f };
    public static readonly float[]? FeatureMeans = new float[] { 0.260273f, 661.473239f, 2711.654930f, 0.354732f };
    public static readonly float[]? FeatureScales = new float[] { 0.176822f, 835.388721f, 889.442340f, 0.305902f };
}
