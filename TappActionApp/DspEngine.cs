using System;

namespace TappActionApp;

/// <summary>
/// Fast digital signal processing routines for hit transient analysis.
/// Implements Radix-2 Real FFT, Spectral Centroid, and High/Low Energy Ratio in pure C#.
/// </summary>
public class DspEngine
{
    private readonly int _n;
    private readonly int _sampleRate;
    private readonly float[] _window;
    private readonly float[] _real;
    private readonly float[] _imag;
    private readonly float[] _mag;
    private readonly float[] _freqs;

    public DspEngine(int blockSize = 512, int sampleRate = 16000)
    {
        _n = blockSize;
        _sampleRate = sampleRate;

        _window = new float[_n];
        _real = new float[_n];
        _imag = new float[_n];
        _mag = new float[_n / 2 + 1];
        _freqs = new float[_n / 2 + 1];

        // Precompute Hanning window and frequency bins
        for (int i = 0; i < _n; i++)
        {
            _window[i] = 0.5f * (1.0f - MathF.Cos(2.0f * MathF.PI * i / (_n - 1)));
        }

        float deltaF = (float)_sampleRate / _n;
        for (int i = 0; i <= _n / 2; i++)
        {
            _freqs[i] = i * deltaF;
        }
    }

    public void ExtractFeatures(
        float[] block,
        out float peak,
        out float energy,
        out float spectralCentroid,
        out float highLowRatio)
    {
        peak = 0.0f;
        float sumSq = 0.0f;

        for (int i = 0; i < _n; i++)
        {
            float val = block[i];
            float abs = MathF.Abs(val);
            if (abs > peak) peak = abs;
            sumSq += val * val;

            _real[i] = val * _window[i];
            _imag[i] = 0.0f;
        }

        energy = sumSq / _n;

        // Compute 512-point Radix-2 FFT
        FftInPlace(_real, _imag);

        // Compute magnitude spectrum for bins 0 .. N/2
        float sumMag = 0.0f;
        float weightedSum = 0.0f;
        float lowEnergy = 0.0f;
        float highEnergy = 0.0f;

        for (int i = 0; i <= _n / 2; i++)
        {
            float r = _real[i];
            float im = _imag[i];
            float m = MathF.Sqrt(r * r + im * im);
            _mag[i] = m;

            sumMag += m;
            weightedSum += _freqs[i] * m;

            float pwr = m * m;
            if (_freqs[i] < 2400.0f)
            {
                lowEnergy += pwr;
            }
            else
            {
                highEnergy += pwr;
            }
        }

        spectralCentroid = (sumMag > 1e-7f) ? (weightedSum / sumMag) : 0.0f;
        highLowRatio = highEnergy / (lowEnergy + 1e-9f);
    }

    private void FftInPlace(float[] real, float[] imag)
    {
        int n = real.Length;

        // Bit-reversal permutation
        int j = 0;
        for (int i = 0; i < n - 1; i++)
        {
            if (i < j)
            {
                float tr = real[i]; real[i] = real[j]; real[j] = tr;
                float ti = imag[i]; imag[i] = imag[j]; imag[j] = ti;
            }
            int k = n >> 1;
            while (k <= j)
            {
                j -= k;
                k >>= 1;
            }
            j += k;
        }

        // Cooley-Tukey decimation-in-time
        for (int len = 2; len <= n; len <<= 1)
        {
            float angle = -2.0f * MathF.PI / len;
            float wstepR = MathF.Cos(angle);
            float wstepI = MathF.Sin(angle);

            for (int i = 0; i < n; i += len)
            {
                float wR = 1.0f;
                float wI = 0.0f;
                int half = len >> 1;

                for (int m = 0; m < half; m++)
                {
                    int uIdx = i + m;
                    int vIdx = i + m + half;

                    float vR = real[vIdx] * wR - imag[vIdx] * wI;
                    float vI = real[vIdx] * wI + imag[vIdx] * wR;

                    real[vIdx] = real[uIdx] - vR;
                    imag[vIdx] = imag[uIdx] - vI;

                    real[uIdx] += vR;
                    imag[uIdx] += vI;

                    float nextWR = wR * wstepR - wI * wstepI;
                    wI = wR * wstepI + wI * wstepR;
                    wR = nextWR;
                }
            }
        }
    }
}
