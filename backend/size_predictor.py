"""
输出文件大小预测模块
采样分析 + 码率估算 双模式
"""
import subprocess
import json
import os
import tempfile
import math
from pathlib import Path


def predict_size(ffmpeg_path, ffprobe_path, input_file, output_params):
    """
    预测压缩后的文件大小
    返回 dict: {predicted_size_mb, compression_ratio, confidence, method, original_size_mb}

    output_params = {
        'video_encoder': 'libx264',
        'crf': 23,
        'audio_encoder': 'aac',
        'audio_bitrate': '128k',
        'output_format': 'mp4',
        'resolution': None,  # or '1920x1080'
    }
    """
    original_size_bytes = os.path.getsize(input_file)
    original_size_mb = round(original_size_bytes / 1048576, 1)

    # 目标体积模式 → 直接计算（精确值）
    if output_params.get('mode') == 'target_size':
        target_pct = int(output_params.get('target_percent', 60))
        predicted_mb = round(original_size_mb * target_pct / 100, 1)
        compression_ratio = round(predicted_mb / original_size_mb, 3) if original_size_mb > 0 else 1.0
        return {
            "original_size_mb": original_size_mb,
            "predicted_size_mb": predicted_mb,
            "savings_mb": round(original_size_mb - predicted_mb, 1),
            "savings_percent": round((1 - compression_ratio) * 100, 1),
            "compression_ratio": compression_ratio,
            "confidence": 1.0,
            "method": "target_size_direct",
        }

    # 获取视频时长
    duration = _get_duration(ffprobe_path, input_file)

    # 短文件 (< 30秒) 或小文件 (< 5MB) → 码率估算
    if original_size_mb < 5 or (duration and duration < 30):
        predicted_mb, confidence = _predict_by_bitrate(
            ffprobe_path, input_file, output_params, original_size_bytes
        )
        method = "bitrate_estimation"
    else:
        # 长文件 → 采样分析 (更准确)
        predicted_mb, confidence = _predict_by_sampling(
            ffmpeg_path, ffprobe_path, input_file, output_params, duration
        )
        method = "sampling"

    compression_ratio = round(predicted_mb / original_size_mb, 3) if original_size_mb > 0 else 1.0

    return {
        "original_size_mb": original_size_mb,
        "predicted_size_mb": round(predicted_mb, 1),
        "savings_mb": round(original_size_mb - predicted_mb, 1),
        "savings_percent": round((1 - compression_ratio) * 100, 1),
        "compression_ratio": compression_ratio,
        "confidence": round(confidence, 2),
        "method": method,
    }


def _get_duration(ffprobe_path, input_file):
    """获取媒体文件时长（秒）"""
    cmd = [
        ffprobe_path, "-v", "quiet",
        "-print_format", "json",
        "-show_format", input_file,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            return float(info.get("format", {}).get("duration", 0))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, subprocess.SubprocessError):
        pass
    return None


def _predict_by_sampling(ffmpeg_path, ffprobe_path, input_file, output_params, duration):
    """采样分析：压缩前30秒片段，推算全片大小"""
    sample_duration = 30
    sample_start = min(duration * 0.1, 60) if duration else 0

    ext = Path(input_file).suffix
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        sample_file = tmp.name

    # 提取前30秒片段
    cut_cmd = [
        ffmpeg_path, "-y", "-ss", str(sample_start),
        "-i", input_file, "-t", str(sample_duration),
        "-c", "copy", sample_file,
    ]

    try:
        subprocess.run(cut_cmd, capture_output=True, text=True, timeout=duration + 30)
    except subprocess.TimeoutExpired:
        os.unlink(sample_file)
        return _predict_by_bitrate(ffprobe_path, input_file, output_params, os.path.getsize(input_file))

    sample_size_before = os.path.getsize(sample_file)

    # 压缩采样片段
    out_ext = {"mp4": ".mp4", "mkv": ".mkv", "mov": ".mov", "webm": ".webm",
               "mp3": ".mp3", "aac": ".aac", "flac": ".flac", "wav": ".wav"}
    sample_out = sample_file.replace(ext, f"_compressed{out_ext.get(output_params.get('output_format', 'mp4'), '.mp4')}")

    compress_cmd = _build_ffmpeg_command(ffmpeg_path, sample_file, sample_out, output_params, False)

    try:
        subprocess.run(compress_cmd, capture_output=True, text=True, timeout=120)
        sample_size_after = os.path.getsize(sample_out)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        sample_size_after = sample_size_before

    # 清理临时文件
    for f in [sample_file, sample_out]:
        if os.path.exists(f):
            os.unlink(f)

    if sample_size_before <= 0:
        return _predict_by_bitrate(ffprobe_path, input_file, output_params, os.path.getsize(input_file))

    ratio = sample_size_after / sample_size_before
    original_size = os.path.getsize(input_file)
    predicted_bytes = original_size * ratio
    predicted_mb = predicted_bytes / 1048576

    # 采样时长越短，置信度越低
    sample_ratio = sample_duration / duration if duration and duration > 0 else 0.3
    confidence = min(0.95, 0.6 + sample_ratio * 0.6)

    return predicted_mb, confidence


def _predict_by_bitrate(ffprobe_path, input_file, output_params, original_size_bytes):
    """码率估算：根据原始码率和CRF值估算输出大小"""
    # 获取原始总码率
    cmd = [
        ffprobe_path, "-v", "quiet",
        "-print_format", "json",
        "-show_format", input_file,
    ]

    original_bitrate = None
    duration = None

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            fmt = info.get("format", {})
            original_bitrate = int(fmt.get("bit_rate", 0))
            duration = float(fmt.get("duration", 0))
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass

    if not duration or duration <= 0:
        return original_size_bytes / 1048576, 0.5

    # CRF值与码率比例关系（经验值）
    crf = int(output_params.get("crf", 23))
    crf_map = {18: 0.85, 20: 0.75, 22: 0.65, 23: 0.6, 24: 0.55, 26: 0.45, 28: 0.35, 30: 0.28}

    idx = min(crf_map.keys(), key=lambda k: abs(k - crf))
    ratio = crf_map.get(idx, 0.6)

    # GPU编码调整 - GPU压缩比通常比CPU低
    encoder = output_params.get("video_encoder", "libx264")
    if "nvenc" in encoder or "amf" in encoder or "qsv" in encoder:
        ratio *= 1.15

    if original_bitrate and original_bitrate > 0:
        output_bitrate = int(original_bitrate * ratio)
        predicted_bytes = output_bitrate * duration / 8
        predicted_mb = predicted_bytes / 1048576
    else:
        predicted_mb = (original_size_bytes / 1048576) * ratio

    confidence = 0.7
    return predicted_mb, confidence


def _build_ffmpeg_command(ffmpeg_path, input_file, output_file, params, show_progress):
    """构建FFmpeg命令"""
    cmd = [ffmpeg_path, "-y", "-i", input_file]

    ext = Path(output_file).suffix.lower()

    if ext in (".mp4", ".mkv", ".mov", ".webm"):
        # 视频
        video_encoder = params.get("video_encoder", "libx264")
        crf = params.get("crf", 23)

        if "nvenc" in video_encoder:
            cmd.extend(["-c:v", video_encoder, "-cq", str(crf), "-preset", "p2"])
        elif "qsv" in video_encoder:
            cmd.extend(["-c:v", video_encoder, "-global_quality", str(crf)])
        elif "amf" in video_encoder:
            cmd.extend(["-c:v", video_encoder, "-quality", "balanced", "-qp_i", str(crf), "-qp_p", str(crf)])
        elif "videotoolbox" in video_encoder:
            cmd.extend(["-c:v", video_encoder, "-q:v", str(crf)])
        else:
            cmd.extend(["-c:v", video_encoder, "-crf", str(crf), "-preset", "medium"])

        # 分辨率调整
        resolution = params.get("resolution")
        if resolution and resolution != "original":
            cmd.extend(["-vf", f"scale={resolution}"])

        # 音频
        audio_encoder = params.get("audio_encoder", "aac")
        audio_bitrate = params.get("audio_bitrate", "128k")
        cmd.extend(["-c:a", audio_encoder, "-b:a", audio_bitrate])

    elif ext in (".mp3", ".aac", ".flac", ".wav"):
        # 纯音频
        audio_encoder = params.get("audio_encoder", "libmp3lame")
        audio_bitrate = params.get("audio_bitrate", "192k")
        cmd.extend(["-c:a", audio_encoder, "-b:a", audio_bitrate])
        cmd.extend(["-vn"])

    if show_progress:
        cmd.extend(["-progress", "pipe:1", "-stats"])

    cmd.append(output_file)
    return cmd
