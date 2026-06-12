"""
FFmpeg 检测与自动安装模块
"""
import os
import sys
import platform
import subprocess
import zipfile
import tarfile
import shutil
import stat
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = BASE_DIR / "bin"


def _get_ffmpeg_path():
    """查找本地ffmpeg可执行文件路径"""
    # 1. 检查系统 PATH
    which_cmd = "where" if platform.system() == "Windows" else "which"
    try:
        result = subprocess.run(
            [which_cmd, "ffmpeg"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 2. 检查本地 bin/ 目录
    ext = ".exe" if platform.system() == "Windows" else ""
    local_path = BIN_DIR / f"ffmpeg{ext}"
    if local_path.exists():
        return str(local_path)

    return None


def _get_ffprobe_path():
    """查找本地ffprobe可执行文件路径"""
    which_cmd = "where" if platform.system() == "Windows" else "which"
    try:
        result = subprocess.run(
            [which_cmd, "ffprobe"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    ext = ".exe" if platform.system() == "Windows" else ""
    local_path = BIN_DIR / f"ffprobe{ext}"
    if local_path.exists():
        return str(local_path)

    return None


def check_installed():
    """检查FFmpeg是否已安装，返回 (已安装: bool, 路径: str|None)"""
    path = _get_ffmpeg_path()
    if not path:
        return False, None

    try:
        result = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, (path, version_line)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return False, None


def detect_hardware():
    """
    检测电脑硬件信息：GPU 型号、CPU 型号
    返回 dict: {
        gpus: [{name, vendor, memory_mb, driver_version}],
        cpu: {model, cores_logical, cores_physical},
        recommended_encoder: "h264_nvenc" | null
    }
    """
    gpus = []
    cpu = {"model": platform.processor() or "Unknown", "cores_logical": os.cpu_count() or 0, "cores_physical": 0}

    if platform.system() == "Windows":
        # --- GPU 检测 ---
        # 方法1: nvidia-smi（最详细）
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2:
                        gpus.append({
                            "name": parts[0],
                            "vendor": "nvidia",
                            "memory_mb": int(float(parts[1])) if parts[1] else 0,
                            "driver_version": parts[2] if len(parts) > 2 else "",
                        })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # 方法2: PowerShell（兜底检测未通过 nvidia-smi 发现的 GPU）
        try:
            ps_cmd = 'Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name'
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    name = line.strip()
                    if name and not any(g["name"].lower() in name.lower() for g in gpus):
                        vendor = "unknown"
                        name_lower = name.lower()
                        if "nvidia" in name_lower:
                            vendor = "nvidia"
                        elif "amd" in name_lower or "radeon" in name_lower:
                            vendor = "amd"
                        elif "intel" in name_lower or "arc" in name_lower:
                            vendor = "intel"
                        gpus.append({
                            "name": name,
                            "vendor": vendor,
                            "memory_mb": 0,
                            "driver_version": "",
                        })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # --- CPU 物理核心数 ---
        try:
            ps_cmd = 'Get-CimInstance Win32_Processor | Select-Object -ExpandProperty NumberOfCores'
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                val = result.stdout.strip()
                if val.isdigit():
                    cpu["cores_physical"] = int(val)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    elif platform.system() == "Linux":
        # Linux: lspci 检测 GPU
        try:
            result = subprocess.run(
                ["lspci"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "VGA" in line or "3D" in line:
                        vendor = "unknown"
                        if "NVIDIA" in line:
                            vendor = "nvidia"
                        elif "AMD" in line or "Radeon" in line:
                            vendor = "amd"
                        elif "Intel" in line:
                            vendor = "intel"
                        gpus.append({
                            "name": line.split(": ")[-1].strip() if ": " in line else line.strip(),
                            "vendor": vendor,
                            "memory_mb": 0,
                            "driver_version": "",
                        })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        cpu["cores_physical"] = cpu["cores_logical"] // 2 if cpu["cores_logical"] > 0 else 0

        # Try to get CPU model from /proc/cpuinfo
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        cpu["model"] = line.split(":")[-1].strip()
                        break
        except Exception:
            pass

    # --- 推荐编码器 ---
    recommended = None
    if gpus:
        # 按优先级：nvidia > amd > intel
        vendor_order = {"nvidia": 0, "amd": 1, "intel": 2}
        sorted_gpus = sorted(gpus, key=lambda g: vendor_order.get(g["vendor"], 99))
        for g in sorted_gpus:
            enc_map = {
                "nvidia": "h264_nvenc",
                "amd": "h264_amf",
                "intel": "h264_qsv",
            }
            recommended = enc_map.get(g["vendor"])
            if recommended:
                break

    return {
        "gpus": gpus,
        "cpu": cpu,
        "recommended_encoder": recommended,
    }


def get_available_encoders(ffmpeg_path, hardware_info=None):
    """检测可用的GPU硬件编码器，返回列表（推荐编码器排在前面）"""
    cpu_encoders = [
        {"id": "libx264", "name": "H.264 (CPU/x264)", "type": "cpu"},
        {"id": "libx265", "name": "H.265/HEVC (CPU/x265)", "type": "cpu"},
    ]

    gpu_encoders = []
    recommended = (hardware_info or {}).get("recommended_encoder", None)

    try:
        result = subprocess.run(
            [ffmpeg_path, "-encoders"], capture_output=True, text=True, timeout=15
        )
        output = result.stdout

        gpu_map = {
            "h264_nvenc": ("H.264 (NVIDIA NVENC)", "nvidia"),
            "hevc_nvenc": ("H.265/HEVC (NVIDIA NVENC)", "nvidia"),
            "av1_nvenc": ("AV1 (NVIDIA NVENC)", "nvidia"),
            "h264_amf": ("H.264 (AMD AMF)", "amd"),
            "hevc_amf": ("H.265/HEVC (AMD AMF)", "amd"),
            "h264_qsv": ("H.264 (Intel QSV)", "intel"),
            "hevc_qsv": ("H.265/HEVC (Intel QSV)", "intel"),
            "h264_videotoolbox": ("H.264 (Apple VideoToolbox)", "apple"),
            "hevc_videotoolbox": ("H.265/HEVC (Apple VideoToolbox)", "apple"),
        }

        for enc_id, (name, vendor) in gpu_map.items():
            if enc_id in output:
                is_recommended = (enc_id == recommended)
                gpu_encoders.append({
                    "id": enc_id,
                    "name": name,
                    "type": "gpu",
                    "vendor": vendor,
                    "recommended": is_recommended,
                })

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    # 推荐编码器排在最前面，然后其他GPU编码器，最后CPU编码器
    gpu_encoders.sort(key=lambda e: (0 if e.get("recommended") else 1))
    return gpu_encoders + cpu_encoders


def get_media_info(ffprobe_path, file_path):
    """使用ffprobe获取媒体文件信息"""
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass
    return None


def download_and_install_ffmpeg(status_callback=None):
    """
    自动下载并安装FFmpeg到 bin/ 目录
    返回 (成功: bool, 路径: str|None, 错误信息: str|None)
    """
    system = platform.system()
    arch = platform.machine().lower()

    if "arm" in arch or "aarch" in arch:
        arch_suffix = "arm64"
    else:
        arch_suffix = "win64" if system == "Windows" else ("x86_64" if system == "Darwin" else "linux64")

    if status_callback:
        status_callback("正在检测系统环境...")

    # 下载地址映射
    urls = []
    if system == "Windows":
        urls = [
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        ]
    elif system == "Darwin":
        urls = [
            "https://evermeet.cx/ffmpeg/get/ffmpeg.zip",
        ]
    else:
        urls = [
            "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
        ]

    BIN_DIR.mkdir(parents=True, exist_ok=True)

    for url in urls:
        if status_callback:
            status_callback(f"正在从 {url.split('/')[2]} 下载 FFmpeg...")

        try:
            import requests
            response = requests.get(url, stream=True, timeout=60)
            if response.status_code != 200:
                continue

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            download_path = BIN_DIR / "ffmpeg_download.zip"

            with open(download_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if status_callback and total_size > 0:
                        pct = int(downloaded / total_size * 100)
                        status_callback(f"正在下载 FFmpeg... {pct}%")

            if status_callback:
                status_callback("正在解压并安装 FFmpeg...")

            # 解压
            if download_path.suffix == ".zip":
                with zipfile.ZipFile(download_path, "r") as zf:
                    zf.extractall(BIN_DIR)
            elif str(download_path).endswith(".tar.xz"):
                with tarfile.open(download_path, "r:xz") as tf:
                    tf.extractall(BIN_DIR)

            download_path.unlink(missing_ok=True)

            # 移动文件到 bin/ 根目录
            for item in BIN_DIR.iterdir():
                if item.is_dir():
                    for f in item.iterdir():
                        if f.name.startswith("ffmpeg") or f.name.startswith("ffprobe"):
                            shutil.copy2(str(f), str(BIN_DIR / f.name))
                            if system != "Windows":
                                os.chmod(str(BIN_DIR / f.name), os.stat(str(BIN_DIR / f.name)).st_mode | stat.S_IEXEC)
                    shutil.rmtree(item)

            # 验证
            ffmpeg_path = _get_ffmpeg_path()
            if ffmpeg_path:
                if status_callback:
                    status_callback("FFmpeg 安装成功！")
                return True, ffmpeg_path, None

        except Exception as e:
            if status_callback:
                status_callback(f"下载失败: {str(e)}")
            continue

    return False, None, "所有下载源均失败，请手动安装 FFmpeg"
