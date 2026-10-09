using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;

namespace TappActionApp;

/// <summary>
/// Native Windows action dispatcher and multi-tap gesture manager (Single, Double, Triple Tap).
/// Sends multimedia and volume key events via user32.dll with zero latency.
/// </summary>
public class ActionDispatcher
{
    private const byte VK_VOLUME_MUTE = 0xAD;
    private const byte VK_VOLUME_DOWN = 0xAE;
    private const byte VK_VOLUME_UP = 0xAF;
    private const byte VK_MEDIA_NEXT_TRACK = 0xB0;
    private const byte VK_MEDIA_PREV_TRACK = 0xB1;
    private const byte VK_MEDIA_PLAY_PAUSE = 0xB3;
    private const byte VK_SNAPSHOT = 0x2C;
    private const uint KEYEVENTF_KEYUP = 0x0002;

    [DllImport("user32.dll")]
    private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    public static readonly string[] AvailableActions = {
        "Volume Up",
        "Volume Down",
        "Mute / Unmute Volume",
        "Play / Pause Media",
        "Next Track",
        "Previous Track",
        "Screenshot (PrintScreen)",
        "None (Detection Only)"
    };

    public bool Enabled { get; set; } = true;
    public string SingleTapAction { get; set; } = "Volume Up";
    public string DoubleTapAction { get; set; } = "Volume Down";
    public string TripleTapAction { get; set; } = "Mute / Unmute Volume";
    public float DoubleTapTimeoutSec { get; set; } = 0.38f;

    private readonly List<double> _tapChain = new List<double>();
    private System.Threading.Timer? _timer;
    private readonly object _lock = new object();

    public event Action<string, string>? OnActionExecuted; // (gestureName, actionName)

    public ActionDispatcher()
    {
        LoadSettings();
    }

    public static bool Execute(string actionName)
    {
        switch (actionName)
        {
            case "Volume Up":
                SendKey(VK_VOLUME_UP);
                return true;
            case "Volume Down":
                SendKey(VK_VOLUME_DOWN);
                return true;
            case "Mute / Unmute Volume":
                SendKey(VK_VOLUME_MUTE);
                return true;
            case "Play / Pause Media":
                SendKey(VK_MEDIA_PLAY_PAUSE);
                return true;
            case "Next Track":
                SendKey(VK_MEDIA_NEXT_TRACK);
                return true;
            case "Previous Track":
                SendKey(VK_MEDIA_PREV_TRACK);
                return true;
            case "Screenshot (PrintScreen)":
                SendKey(VK_SNAPSHOT);
                return true;
            case "None (Detection Only)":
                return true;
            default:
                return false;
        }
    }

    private static void SendKey(byte vkCode)
    {
        keybd_event(vkCode, 0, 0, UIntPtr.Zero);
        Thread.Sleep(10);
        keybd_event(vkCode, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
    }

    public static bool IsActionConfigured(string? action)
    {
        return !string.IsNullOrWhiteSpace(action) &&
               !action.StartsWith("None", StringComparison.OrdinalIgnoreCase) &&
               !action.Equals("Disabled", StringComparison.OrdinalIgnoreCase);
    }

    public void OnHitDetected()
    {
        if (!Enabled) return;

        double now = DateTime.UtcNow.Ticks / (double)TimeSpan.TicksPerSecond;

        lock (_lock)
        {
            // Cancel pending timers
            _timer?.Dispose();
            _timer = null;

            bool hasDouble = IsActionConfigured(DoubleTapAction);
            bool hasTriple = IsActionConfigured(TripleTapAction);
            bool hasSingle = IsActionConfigured(SingleTapAction);

            // Instant single-tap mode:
            // When neither double nor triple tap actions are configured,
            // fire single tap immediately with zero buffering latency and zero canceled taps!
            if (!hasDouble && !hasTriple)
            {
                _tapChain.Clear();
                if (hasSingle)
                {
                    Dispatch("Single Tap", SingleTapAction);
                }
                return;
            }

            // Reset chain if last tap was too long ago
            if (_tapChain.Count > 0 && (now - _tapChain[^1]) > DoubleTapTimeoutSec)
            {
                _tapChain.Clear();
            }

            _tapChain.Add(now);
            int count = _tapChain.Count;

            if (count == 1)
            {
                if (hasSingle)
                {
                    // First tap: wait timeout before deciding single tap
                    int dueMs = (int)(DoubleTapTimeoutSec * 1000);
                    _timer = new System.Threading.Timer(_ => FinalizeSingleTap(), null, dueMs, Timeout.Infinite);
                }
            }
            else if (count == 2)
            {
                if (!hasTriple)
                {
                    // Triple tap is disabled: fire double tap immediately!
                    _tapChain.Clear();
                    if (hasDouble)
                    {
                        Dispatch("Double Tap", DoubleTapAction);
                    }
                }
                else
                {
                    // Triple tap is enabled: wait small window in case user does triple tap
                    int dueMs = (int)(DoubleTapTimeoutSec * 1000);
                    _timer = new System.Threading.Timer(_ => FinalizeDoubleTap(), null, dueMs, Timeout.Infinite);
                }
            }
            else if (count >= 3)
            {
                // Third tap: fire triple tap immediately!
                _tapChain.Clear();
                if (hasTriple)
                {
                    Dispatch("Triple Tap", TripleTapAction);
                }
            }
        }
    }

    private void FinalizeSingleTap()
    {
        lock (_lock)
        {
            if (_tapChain.Count == 1)
            {
                _tapChain.Clear();
                Dispatch("Single Tap", SingleTapAction);
            }
        }
    }

    private void FinalizeDoubleTap()
    {
        lock (_lock)
        {
            if (_tapChain.Count == 2)
            {
                _tapChain.Clear();
                Dispatch("Double Tap", DoubleTapAction);
            }
        }
    }

    private void Dispatch(string gesture, string action)
    {
        if (!Enabled || action == "None (Detection Only)") return;

        Execute(action);
        OnActionExecuted?.Invoke(gesture, action);
    }

    public void LoadSettings(string? filePath = null)
    {
        string[] searchPaths = {
            filePath ?? "",
            "data/actions_config.json",
            "../data/actions_config.json",
            "../../data/actions_config.json",
            "../../../data/actions_config.json",
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data", "actions_config.json"),
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "..", "..", "data", "actions_config.json")
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
                    if (root.TryGetProperty("enabled", out var en)) Enabled = en.GetBoolean();
                    if (root.TryGetProperty("single_tap_action", out var st)) SingleTapAction = st.GetString() ?? SingleTapAction;
                    if (root.TryGetProperty("double_tap_action", out var dt)) DoubleTapAction = dt.GetString() ?? DoubleTapAction;
                    if (root.TryGetProperty("triple_tap_action", out var tt)) TripleTapAction = tt.GetString() ?? TripleTapAction;
                    if (root.TryGetProperty("double_tap_timeout_sec", out var to)) DoubleTapTimeoutSec = (float)to.GetDouble();
                    break;
                }
                catch { }
            }
        }
    }

    public void SaveSettings(string? filePath = null)
    {
        string target = filePath ?? "data/actions_config.json";
        try
        {
            var dir = Path.GetDirectoryName(target);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

            var dict = new Dictionary<string, object>
            {
                ["enabled"] = Enabled,
                ["single_tap_action"] = SingleTapAction,
                ["double_tap_action"] = DoubleTapAction,
                ["triple_tap_action"] = TripleTapAction,
                ["double_tap_timeout_sec"] = DoubleTapTimeoutSec
            };

            string json = JsonSerializer.Serialize(dict, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(target, json);
        }
        catch { }
    }
}
