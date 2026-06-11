"""
压缩引擎 - 负责执行FFmpeg压缩任务，解析进度
"""
import subprocess
import threading
import json
import time
import os
import re
from pathlib import Path


class CompressionTask:
    """单个压缩任务"""

    def __init__(self, task_id, input_file, output_file, params):
        self.task_id = task_id
        self.input_file = input_file
        self.output_file = output_file
        self.params = params
        self.progress = 0.0          # 0-100
        self.status = "pending"      # pending → running → completed/failed
        self.error = None
        self.output_size = 0
        self.process = None
        self._duration = None        # 总时长（秒）
        self._start_time = None
        self._elapsed = 0

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "input_file": Path(self.input_file).name,
            "output_file": Path(self.output_file).name,
            "progress": round(self.progress, 1),
            "status": self.status,
            "error": self.error,
            "output_size": self.output_size,
            "elapsed": round(self._elapsed, 1),
        }


class CompressionEngine:
    """压缩引擎，管理多个任务"""

    def __init__(self, ffmpeg_path, ffprobe_path):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.tasks = {}
        self._callbacks = {}     # task_id → progress_callback

    def start_task(self, task, progress_callback=None):
        """启动一个压缩任务"""
        self.tasks[task.task_id] = task
        task.status = "running"
        task._start_time = time.time()

        if progress_callback:
            self._callbacks[task.task_id] = progress_callback

        thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        thread.start()
        return task

    def _run_task(self, task):
        """在线程中执行压缩"""
        try:
            cmd = self._build_command(task)
            task.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # 获取视频总时长
            self._get_duration(task)

            # 解析进度
            time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
            speed_pattern = re.compile(r"speed=\s*([\d.]+)x")

            for line in iter(task.process.stderr.readline, ""):
                # 解析 time=00:01:23.45
                m = time_pattern.search(line)
                if m and task._duration and task._duration > 0:
                    h, mi, s, ms = map(int, m.groups())
                    current_time = h * 3600 + mi * 60 + s + ms / 100
                    task.progress = min(98.0, current_time / task._duration * 100)

                # 解析 speed=
                m = speed_pattern.search(line)
                if m and task._start_time:
                    task._elapsed = time.time() - task._start_time

                # 回调
                cb = self._callbacks.get(task.task_id)
                if cb:
                    cb(task.to_dict())

            task.process.wait()

            if task.process.returncode == 0:
                task.progress = 100.0
                task.status = "completed"
                if os.path.exists(task.output_file):
                    task.output_size = os.path.getsize(task.output_file)
            else:
                task.status = "failed"
                stderr_output = task.process.stderr.read() if task.process.stderr else ""
                task.error = f"FFmpeg 返回错误码 {task.process.returncode}"
                if "No such file" in stderr_output:
                    task.error = "文件不存在或路径错误"
                elif "Invalid data found" in stderr_output:
                    task.error = "文件格式不支持或已损坏"
                elif "encoder not found" in stderr_output:
                    task.error = "编码器不可用，请选择其他编码器"

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
        finally:
            task._elapsed = time.time() - task._start_time if task._start_time else 0
            cb = self._callbacks.get(task.task_id)
            if cb:
                cb(task.to_dict())
            self._callbacks.pop(task.task_id, None)

    def _get_duration(self, task):
        """获取文件总时长"""
        cmd = [
            self.ffprobe_path, "-v", "quiet",
            "-print_format", "json",
            "-show_format", task.input_file,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                info = json.loads(result.stdout)
                task._duration = float(info.get("format", {}).get("duration", 0))
        except Exception:
            pass

    def _build_command(self, task):
        """根据参数构建FFmpeg命令"""
        p = task.params
        cmd = [self.ffmpeg_path, "-y", "-i", task.input_file]

        out_ext = Path(task.output_file).suffix.lower()

        if out_ext in (".mp4", ".mkv", ".mov", ".webm"):
            video_encoder = p.get("video_encoder", "libx264")
            crf = p.get("crf", 23)

            if "nvenc" in video_encoder:
                cmd.extend(["-c:v", video_encoder, "-cq", str(crf), "-preset", "p7" if "av1" in video_encoder else "p4"])
            elif "qsv" in video_encoder:
                cmd.extend(["-c:v", video_encoder, "-global_quality", str(crf)])
            elif "amf" in video_encoder:
                cmd.extend(["-c:v", video_encoder, "-quality", "balanced", "-qp_i", str(crf), "-qp_p", str(crf)])
            elif "videotoolbox" in video_encoder:
                cmd.extend(["-c:v", video_encoder, "-q:v", str(crf)])
            else:
                cmd.extend(["-c:v", video_encoder, "-crf", str(crf), "-preset", "medium"])

            # 分辨率
            resolution = p.get("resolution")
            if resolution and resolution not in ("original", "", None):
                cmd.extend(["-vf", f"scale={resolution}"])

            audio_encoder = p.get("audio_encoder", "aac")
            audio_bitrate = p.get("audio_bitrate", "128k")
            cmd.extend(["-c:a", audio_encoder, "-b:a", audio_bitrate])

        elif out_ext in (".mp3", ".aac", ".flac", ".wav"):
            audio_encoder = p.get("audio_encoder", "libmp3lame")
            audio_bitrate = p.get("audio_bitrate", "192k")
            cmd.extend(["-c:a", audio_encoder, "-b:a", audio_bitrate, "-vn"])

        cmd.append(task.output_file)
        return cmd

    def cancel_task(self, task_id):
        """取消任务"""
        task = self.tasks.get(task_id)
        if task and task.process and task.process.poll() is None:
            task.process.terminate()
            task.status = "cancelled"
            return True
        return False

    def get_task(self, task_id):
        return self.tasks.get(task_id)
