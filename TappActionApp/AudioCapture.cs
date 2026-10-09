using System;
using System.Runtime.InteropServices;

namespace TappActionApp;

/// <summary>
/// Native Windows waveIn audio capture with ultra-low latency and zero external dependencies.
/// Uses winmm.dll to stream 16 kHz 16-bit PCM mono audio directly from the laptop microphone.
/// </summary>
public class AudioCapture : IDisposable
{
    private const int WAVE_MAPPER = -1;
    private const int CALLBACK_FUNCTION = 0x00030000;
    private const int WIM_DATA = 0x3C0;
    private const int WIM_OPEN = 0x3BE;
    private const int WIM_CLOSE = 0x3BF;

    [StructLayout(LayoutKind.Sequential)]
    public struct WAVEFORMATEX
    {
        public ushort wFormatTag;
        public ushort nChannels;
        public uint nSamplesPerSec;
        public uint nAvgBytesPerSec;
        public ushort nBlockAlign;
        public ushort wBitsPerSample;
        public ushort cbSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct WAVEHDR
    {
        public IntPtr lpData;
        public uint dwBufferLength;
        public uint dwBytesRecorded;
        public IntPtr dwUser;
        public uint dwFlags;
        public uint dwLoops;
        public IntPtr lpNext;
        public IntPtr reserved;
    }

    private delegate void WaveInProc(IntPtr hwi, uint uMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInOpen(out IntPtr phwi, int uDeviceID, ref WAVEFORMATEX lpFormat, WaveInProc dwCallback, IntPtr dwInstance, int fdwOpen);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInPrepareHeader(IntPtr hwi, IntPtr pwh, int cbwh);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInUnprepareHeader(IntPtr hwi, IntPtr pwh, int cbwh);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInAddBuffer(IntPtr hwi, IntPtr pwh, int cbwh);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInStart(IntPtr hwi);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInStop(IntPtr hwi);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInReset(IntPtr hwi);

    [DllImport("winmm.dll", SetLastError = true)]
    private static extern int waveInClose(IntPtr hwi);

    private IntPtr _hWaveIn = IntPtr.Zero;
    private WaveInProc? _callback;
    private readonly int _sampleRate;
    private readonly int _blockSize;
    private const int BufferCount = 3;

    private readonly GCHandle[] _bufferHandles = new GCHandle[BufferCount];
    private readonly GCHandle[] _headerHandles = new GCHandle[BufferCount];
    private readonly byte[][] _rawBuffers = new byte[BufferCount][];
    private readonly WAVEHDR[] _headers = new WAVEHDR[BufferCount];
    private readonly float[] _sampleBuffer;

    private bool _isRecording = false;
    private readonly object _lock = new object();

    public event Action<float[]>? OnAudioBlock;
    public bool IsRecording => _isRecording;
    public string? ErrorMessage { get; private set; }

    public AudioCapture(int sampleRate = 16000, int blockSize = 512)
    {
        _sampleRate = sampleRate;
        _blockSize = blockSize;
        _sampleBuffer = new float[_blockSize];
    }

    public bool Start(int deviceId = WAVE_MAPPER)
    {
        lock (_lock)
        {
            if (_isRecording) return true;

            var format = new WAVEFORMATEX
            {
                wFormatTag = 1, // PCM
                nChannels = 1,  // Mono
                nSamplesPerSec = (uint)_sampleRate,
                wBitsPerSample = 16,
                nBlockAlign = 2,
                nAvgBytesPerSec = (uint)(_sampleRate * 2),
                cbSize = 0
            };

            _callback = new WaveInProc(WaveInCallback);

            int res = waveInOpen(out _hWaveIn, deviceId, ref format, _callback, IntPtr.Zero, CALLBACK_FUNCTION);
            if (res != 0)
            {
                ErrorMessage = $"waveInOpen failed with error code {res}";
                return false;
            }

            int bufferSizeBytes = _blockSize * 2; // 16-bit = 2 bytes per sample

            for (int i = 0; i < BufferCount; i++)
            {
                _rawBuffers[i] = new byte[bufferSizeBytes];
                _bufferHandles[i] = GCHandle.Alloc(_rawBuffers[i], GCHandleType.Pinned);

                _headers[i] = new WAVEHDR
                {
                    lpData = _bufferHandles[i].AddrOfPinnedObject(),
                    dwBufferLength = (uint)bufferSizeBytes,
                    dwBytesRecorded = 0,
                    dwUser = (IntPtr)i,
                    dwFlags = 0
                };

                _headerHandles[i] = GCHandle.Alloc(_headers[i], GCHandleType.Pinned);

                waveInPrepareHeader(_hWaveIn, _headerHandles[i].AddrOfPinnedObject(), Marshal.SizeOf<WAVEHDR>());
                waveInAddBuffer(_hWaveIn, _headerHandles[i].AddrOfPinnedObject(), Marshal.SizeOf<WAVEHDR>());
            }

            res = waveInStart(_hWaveIn);
            if (res != 0)
            {
                ErrorMessage = $"waveInStart failed with error code {res}";
                Stop();
                return false;
            }

            _isRecording = true;
            ErrorMessage = null;
            return true;
        }
    }

    public void Stop()
    {
        lock (_lock)
        {
            if (!_isRecording && _hWaveIn == IntPtr.Zero) return;
            _isRecording = false;

            if (_hWaveIn != IntPtr.Zero)
            {
                waveInStop(_hWaveIn);
                waveInReset(_hWaveIn);

                for (int i = 0; i < BufferCount; i++)
                {
                    if (_headerHandles[i].IsAllocated)
                    {
                        waveInUnprepareHeader(_hWaveIn, _headerHandles[i].AddrOfPinnedObject(), Marshal.SizeOf<WAVEHDR>());
                        _headerHandles[i].Free();
                    }
                    if (_bufferHandles[i].IsAllocated)
                    {
                        _bufferHandles[i].Free();
                    }
                }

                waveInClose(_hWaveIn);
                _hWaveIn = IntPtr.Zero;
            }
        }
    }

    private void WaveInCallback(IntPtr hwi, uint uMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2)
    {
        if (uMsg == WIM_DATA && _isRecording && dwParam1 != IntPtr.Zero)
        {
            var header = Marshal.PtrToStructure<WAVEHDR>(dwParam1);
            int bytesRecorded = (int)header.dwBytesRecorded;
            int samplesRecorded = bytesRecorded / 2;

            if (samplesRecorded > 0)
            {
                int bufIdx = (int)header.dwUser;
                byte[] raw = _rawBuffers[bufIdx];

                int count = Math.Min(samplesRecorded, _blockSize);
                for (int i = 0; i < count; i++)
                {
                    short val = (short)(raw[i * 2] | (raw[i * 2 + 1] << 8));
                    _sampleBuffer[i] = val / 32768.0f;
                }

                OnAudioBlock?.Invoke(_sampleBuffer);
            }

            // Re-queue buffer for continuous streaming
            if (_isRecording && _hWaveIn != IntPtr.Zero)
            {
                waveInAddBuffer(_hWaveIn, dwParam1, Marshal.SizeOf<WAVEHDR>());
            }
        }
    }

    public void Dispose()
    {
        Stop();
    }
}
