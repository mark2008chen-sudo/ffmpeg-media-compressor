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


def get_available_encoders(ffmpeg_path):
    """检测可用的GPU硬件编码器，返回列表"""
    cpu_encoders = [
        {"id": "libx264", "name": "H.264 (CPU/x264)", "type": "cpu"},
        {"id": "libx265", "name": "H.265/HEVC (CPU/x265)", "type": "cpu"},
    ]

    gpu_encoders = []

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
                gpu_encoders.append({"id": enc_id, "name": name, "type": "gpu", "vendor": vendor})

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    return cpu_encoders + gpu_encoders


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
