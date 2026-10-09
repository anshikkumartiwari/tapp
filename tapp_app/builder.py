"""Build and desktop deployment engine for Tapp standalone executable.

Generates hardcoded C# calibrated profile, compiles native binary with dotnet,
copies standalone tapp.exe, and creates desktop shortcuts.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

from .detector import DetectorConfig


def generate_csharp_profile(cfg: DetectorConfig, output_path: Optional[Path] = None) -> Path:
    """Generate CalibratedProfile.cs with hardcoded calibrated values."""
    if output_path is None:
        output_path = Path("TappActionApp") / "CalibratedProfile.cs"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    def format_float_array(arr: Optional[list]) -> str:
        if not arr:
            return "null"
        vals = ", ".join(f"{float(x):.6f}f" for x in arr)
        return f"new float[] {{ {vals} }}"

    weights_str = format_float_array(cfg.classifier_weights)
    means_str = format_float_array(cfg.feature_means)
    scales_str = format_float_array(cfg.feature_scales)

    code = f"""namespace TappActionApp;

/// <summary>
/// Hardcoded acoustic calibration profile baked directly into the standalone binary.
/// Generated automatically during the guided calibration wizard.
/// </summary>
public static class CalibratedProfile
{{
    public const float AmplitudeFloor = {float(cfg.amplitude_floor):.4f}f;
    public const float AttackRatioThreshold = {float(cfg.attack_ratio_threshold):.4f}f;
    public const float CentroidMaxHz = {float(cfg.centroid_max_hz):.1f}f;
    public const float HighLowRatioMax = {float(cfg.high_low_ratio_max):.4f}f;
    public const float RefractorySec = {float(cfg.refractory_sec):.4f}f;
    public const float MinConfidence = {float(cfg.min_confidence):.4f}f;
    public const float ClassifierBias = {float(cfg.classifier_bias):.4f}f;

    public static readonly float[]? ClassifierWeights = {weights_str};
    public static readonly float[]? FeatureMeans = {means_str};
    public static readonly float[]? FeatureScales = {scales_str};
}}
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)

    return output_path


def find_dotnet_executable() -> Optional[str]:
    """Locate dotnet.exe on Windows."""
    # 1. System PATH
    found = shutil.which("dotnet")
    if found:
        return found

    # 2. Local AppData default dotnet install location
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        cand = Path(local_appdata) / "Microsoft" / "dotnet" / "dotnet.exe"
        if cand.exists():
            return str(cand)

    # 3. Standard Program Files
    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    cand = Path(prog_files) / "dotnet" / "dotnet.exe"
    if cand.exists():
        return str(cand)

    return None


def build_standalone_executable(cfg: Optional[DetectorConfig] = None, root_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """Compile TappActionApp into a standalone tapp.exe with hardcoded calibration."""
    if cfg is None:
        from .calibration import load_calibration
        loaded_cfg, _ = load_calibration()
        cfg = loaded_cfg or DetectorConfig()

    if root_dir is None:
        root_dir = Path.cwd()

    csproj = root_dir / "TappActionApp" / "TappActionApp.csproj"
    if not csproj.exists():
        return False, f"Project file not found at {csproj}"

    dotnet = find_dotnet_executable()
    if not dotnet:
        return False, ".NET SDK 8.0 or newer was not found. Please install .NET 8 SDK."

    # 1. Bake calibration into CalibratedProfile.cs
    profile_cs = root_dir / "TappActionApp" / "CalibratedProfile.cs"
    generate_csharp_profile(cfg, profile_cs)

    # 2. Publish release build
    publish_dir = root_dir / "TappActionApp" / "publish"
    cmd = [
        dotnet,
        "publish",
        str(csproj),
        "-c",
        "Release",
        "-r",
        "win-x64",
        "--no-self-contained",
        "-o",
        str(publish_dir),
    ]

    try:
        res = subprocess.run(cmd, cwd=str(root_dir), capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            return False, f"Build error:\n{res.stderr or res.stdout}"
    except Exception as e:
        return False, f"Failed to execute build: {e}"

    src_exe = publish_dir / "tapp.exe"
    if not src_exe.exists():
        return False, "Build completed but tapp.exe was not created in output directory."

    # 3. Copy standalone tapp.exe to root folder
    dst_exe = root_dir / "tapp.exe"
    try:
        shutil.copy2(src_exe, dst_exe)
    except Exception:
        pass

    return True, str(dst_exe if dst_exe.exists() else src_exe)


def get_desktop_directories() -> List[Path]:
    """Identify all valid Desktop folders for the current Windows user."""
    candidates: List[Path] = []

    # 1. Windows Registry (User Shell Folders Desktop - authoritative)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            val, _ = winreg.QueryValueEx(key, "Desktop")
            expanded = Path(os.path.expandvars(val)).resolve()
            if expanded.exists() and expanded not in candidates:
                candidates.append(expanded)
    except Exception:
        pass

    # 2. OneDrive Desktop
    onedrive_desktop = (Path.home() / "OneDrive" / "Desktop").resolve()
    if onedrive_desktop.exists() and onedrive_desktop not in candidates:
        candidates.append(onedrive_desktop)

    # 3. Standard local Desktop
    local_desktop = (Path.home() / "Desktop").resolve()
    if local_desktop.exists() and local_desktop not in candidates:
        candidates.append(local_desktop)

    if not candidates:
        candidates.append(local_desktop)
    return candidates


def create_desktop_shortcut(target_exe: str, shortcut_name: str = "Tapp.lnk") -> Tuple[bool, str]:
    """Create a Windows desktop shortcut pointing to the standalone tapp.exe."""
    target_path = Path(target_exe).resolve()
    if not target_path.exists():
        return False, f"Target executable not found: {target_path}"

    work_dir = target_path.parent
    desktops = get_desktop_directories()
    created_paths: List[Path] = []

    for desktop in desktops:
        shortcut_path = desktop / shortcut_name
        vbs_content = f"""Set w = WScript.CreateObject("WScript.Shell")
Set s = w.CreateShortcut("{shortcut_path}")
s.TargetPath = "{target_path}"
s.WorkingDirectory = "{work_dir}"
s.Description = "Tapp - Palm-Rest Tap Action Trigger"
s.Save
"""
        tmp_vbs = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".vbs", delete=False, encoding="utf-8") as f:
                f.write(vbs_content)
                tmp_vbs = f.name

            subprocess.run(["cscript", "//nologo", tmp_vbs], capture_output=True, check=True, timeout=10)
            if shortcut_path.exists():
                created_paths.append(shortcut_path)
        except Exception:
            pass
        finally:
            if tmp_vbs and os.path.exists(tmp_vbs):
                try:
                    os.remove(tmp_vbs)
                except Exception:
                    pass

    if created_paths:
        return True, str(created_paths[0])
    return False, "Shortcut file could not be created on Desktop."
