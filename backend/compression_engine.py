"""
压缩引擎 - 负责执行FFmpeg压缩任务，解析进度
"""
import subprocess
import threading
import json
import time
import os
import re
import queue
from pathlib import Path


def _stderr_reader(process, line_queue):
    """后台线程：持续读取 stderr，将每行放入队列；EOF 时放入 None"""
    try:
        for line in iter(process.stderr.readline, ""):
            line_queue.put(line)
    except Exception:
        pass
    line_queue.put(None)  # EOF 哨兵


class CompressionTask:
    """单个压缩任务"""

    def __init__(self, task_id, input_file, output_file, params):
        self.task_id = task_id
        self.input_file = input_file
        self.output_file = output_file
        self.params = params
        self.progress = 0.0          # 0-100
        self.status = "pending"      # pending → running → completed/failed/cancelled
        self.error = None
        self.output_size = 0
        self.original_size = os.path.getsize(input_file) if os.path.exists(input_file) else 0
        self.speed = 0.0             # FFmpeg speed (e.g. 2.3x)
        self.phase = "pending"       # pending | encoding | analyzing | completed | failed | cancelled
        self.process = None
        self._cancelled = False      # 取消标志，编码循环检查此标志即时停止
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
            "phase": self.phase,
            "error": self.error,
            "output_size": self.output_size,
            "original_size": self.original_size,
            "speed": round(self.speed, 2),
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
            # 启动前检查是否已被取消
            if task._cancelled:
                return
            if task.params.get('mode') == 'target_size':
                self._run_task_2pass(task)
            else:
                self._run_task_singlepass(task)
        except Exception as e:
            if not task._cancelled:
                task.status = "failed"
                task.phase = "failed"
                task.error = str(e)
        finally:
            task._elapsed = time.time() - task._start_time if task._start_time else 0
            self._clean_pass_logs()
            # 清理被取消或失败任务的不完整输出文件
            if task._cancelled and os.path.exists(task.output_file):
                try:
                    os.unlink(task.output_file)
                except OSError:
                    pass
            if task.status == "failed" and os.path.exists(task.output_file):
                try:
                    os.unlink(task.output_file)
                except OSError:
                    pass
            cb = self._callbacks.get(task.task_id)
            if cb:
                cb(task.to_dict())
            self._callbacks.pop(task.task_id, None)

    def _run_task_singlepass(self, task):
        """单遍CRF编码"""
        task.phase = "encoding"
        cmd = self._build_command(task)
        task.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # 获取视频总时长
        self._get_duration(task)

        # 后台线程读取 stderr，主线程轮询进程状态 + 消费队列
        line_queue = queue.Queue()
        reader = threading.Thread(target=_stderr_reader, args=(task.process, line_queue), daemon=True)
        reader.start()

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        speed_pattern = re.compile(r"speed=\s*([\d.]+)x")
        _stderr_eof = False
        _last_output_time = time.time()  # 用于检测进程僵死

        while not _stderr_eof:
            if task._cancelled:
                task.process.kill()
                break

            # 非阻塞消费队列中的所有行
            while True:
                try:
                    line = line_queue.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    _stderr_eof = True
                    break

                _last_output_time = time.time()

                m = time_pattern.search(line)
                if m and task._duration and task._duration > 0:
                    h, mi, s, ms = map(int, m.groups())
                    current_time = h * 3600 + mi * 60 + s + ms / 100
                    task.progress = min(99.0, current_time / task._duration * 100)

                m = speed_pattern.search(line)
                if m:
                    task.speed = float(m.group(1))

            # 僵死检测：60 秒无 stderr 输出且进程存活 → 强制终止
            if task._elapsed > 10 and time.time() - _last_output_time > 60:
                task.error = "编码进程疑似僵死（60秒无进度更新），已强制终止"
                task.process.kill()
                break

            if task._start_time:
                task._elapsed = time.time() - task._start_time

            cb = self._callbacks.get(task.task_id)
            if cb:
                cb(task.to_dict())

            time.sleep(0.1)

        reader.join(timeout=2)

        # 编码 stderr 已终止，等待 ffmpeg 完成文件封装（muxing）
        if not task._cancelled:
            task.progress = min(99.5, task.progress)
            while task.process.poll() is None:
                if task._cancelled:
                    task.process.kill()
                    break
                if task._start_time:
                    task._elapsed = time.time() - task._start_time
                cb = self._callbacks.get(task.task_id)
                if cb:
                    cb(task.to_dict())
                time.sleep(0.2)

        task.process.wait()

        # 被取消则跳过状态设置（cancel_task 已设置）
        if task._cancelled:
            return

        if task.process.returncode == 0:
            task.progress = 100.0
            task.phase = "completed"
            task.status = "completed"
            if os.path.exists(task.output_file):
                task.output_size = os.path.getsize(task.output_file)
                if task.output_size < 1024:
                    task.status = "failed"
                    task.phase = "failed"
                    task.error = "编码输出异常（文件过小，可能编码中断）"
                    task.output_size = 0
        else:
            task.status = "failed"
            task.phase = "failed"
            stderr_output = task.process.stderr.read() if task.process.stderr else ""
            task.error = f"FFmpeg 返回错误码 {task.process.returncode}"
            if "No such file" in stderr_output:
                task.error = "文件不存在或路径错误"
            elif "Invalid data found" in stderr_output:
                task.error = "文件格式不支持或已损坏"
            elif "encoder not found" in stderr_output:
                task.error = "编码器不可用，请选择其他编码器"

    def _run_task_2pass(self, task):
        """2-pass VBR编码（目标体积模式）"""
        target_pct = int(task.params.get('target_percent', 60))

        # 获取视频时长
        self._get_duration(task)
        if not task._duration or task._duration <= 0:
            task.status = "failed"
            task.phase = "failed"
            task.error = "无法获取视频时长，目标体积压缩需要时长信息"
            return

        # 计算目标视频码率
        target_bitrate = self._calculate_target_bitrate(task, target_pct)
        if target_bitrate < 100000:
            task.status = "failed"
            task.phase = "failed"
            task.error = f"目标码率过低（{target_bitrate//1000}kbps），请选择更大的百分比或减小音频码率"
            return

        # Pass 1：视频分析（合并到编码进度中，不单独显示"分析中"）
        task.phase = "encoding"
        pass1_cmd = self._build_pass1_cmd(task, target_bitrate)
        task.process = subprocess.Popen(
            pass1_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while task.process.poll() is None:
                if task._cancelled:
                    task.process.kill()
                    task.process.wait()
                    self._clean_pass_logs()
                    return
                if task._start_time:
                    task._elapsed = time.time() - task._start_time
                cb = self._callbacks.get(task.task_id)
                if cb:
                    cb(task.to_dict())
                time.sleep(0.1)
            if task.process.returncode != 0:
                task.status = "failed"
                task.phase = "failed"
                task.error = f"2-pass 第一遍分析失败（返回码 {task.process.returncode}）"
                self._clean_pass_logs()
                return
        except Exception:
            if task._cancelled:
                self._clean_pass_logs()
                return
            task.status = "failed"
            task.phase = "failed"
            task.error = "2-pass 第一遍分析异常"
            self._clean_pass_logs()
            return
        task.progress = 50.0
        cb = self._callbacks.get(task.task_id)
        if cb:
            cb(task.to_dict())

        # Pass 2：正式编码（后台线程读 stderr，主线程轮询）
        task.phase = "encoding"
        pass2_cmd = self._build_pass2_cmd(task, target_bitrate)
        task.process = subprocess.Popen(
            pass2_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        line_queue = queue.Queue()
        reader = threading.Thread(target=_stderr_reader, args=(task.process, line_queue), daemon=True)
        reader.start()

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        speed_pattern = re.compile(r"speed=\s*([\d.]+)x")
        _stderr_eof = False
        _last_output_time = time.time()

        while not _stderr_eof:
            if task._cancelled:
                task.process.kill()
                break

            while True:
                try:
                    line = line_queue.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    _stderr_eof = True
                    break

                _last_output_time = time.time()

                m = time_pattern.search(line)
                if m and task._duration and task._duration > 0:
                    h, mi, s, ms = map(int, m.groups())
                    current_time = h * 3600 + mi * 60 + s + ms / 100
                    task.progress = min(99.0, 50 + 50 * current_time / task._duration)

                m = speed_pattern.search(line)
                if m:
                    task.speed = float(m.group(1))

            if task._start_time:
                task._elapsed = time.time() - task._start_time

            # 僵死检测：60 秒无 stderr 输出且进程存活 → 强制终止
            if task._elapsed > 10 and time.time() - _last_output_time > 60:
                task.error = "编码进程疑似僵死（60秒无进度更新），已强制终止"
                task.process.kill()
                break

            cb = self._callbacks.get(task.task_id)
            if cb:
                cb(task.to_dict())

            time.sleep(0.1)

        reader.join(timeout=2)

        # 编码 stderr 已终止，等待 ffmpeg 完成文件封装（muxing）
        if not task._cancelled:
            task.progress = min(99.5, task.progress)
            while task.process.poll() is None:
                if task._cancelled:
                    task.process.kill()
                    break
                if task._start_time:
                    task._elapsed = time.time() - task._start_time
                cb = self._callbacks.get(task.task_id)
                if cb:
                    cb(task.to_dict())
                time.sleep(0.2)

        task.process.wait()
        self._clean_pass_logs()

        if task._cancelled:
            return

        if task.process.returncode == 0:
            task.progress = 100.0
            task.phase = "completed"
            task.status = "completed"
            if os.path.exists(task.output_file):
                task.output_size = os.path.getsize(task.output_file)
                if task.output_size < 1024:
                    task.status = "failed"
                    task.phase = "failed"
                    task.error = "编码输出异常（文件过小，可能编码中断）"
                    task.output_size = 0
        else:
            task.status = "failed"
            task.phase = "failed"
            task.error = f"FFmpeg 第二遍编码返回错误码 {task.process.returncode}"

    def _calculate_target_bitrate(self, task, target_pct):
        """根据目标百分比计算视频码率（bps）"""
        p = task.params
        original_size = os.path.getsize(task.input_file)
        duration = task._duration

        target_total = original_size * target_pct / 100
        target_bitrate_bps = target_total * 8 / duration

        audio_bitrate_str = p.get('audio_bitrate', '128k')
        audio_bps = 128000
        if audio_bitrate_str.endswith('k'):
            try:
                audio_bps = int(audio_bitrate_str[:-1]) * 1000
            except ValueError:
                audio_bps = 128000

        video_bps = int(target_bitrate_bps - audio_bps)
        return max(video_bps, 100000)

    def _build_pass1_cmd(self, task, video_bitrate):
        """构建2-pass第一遍（分析）命令"""
        p = task.params
        encoder = p.get('video_encoder', 'libx264')
        bitrate_str = f"{video_bitrate // 1000}k"

        if "nvenc" in encoder:
            preset = "p4" if "av1" in encoder else "p2"
            vcodec_opts = ["-c:v", encoder, "-b:v", bitrate_str, "-preset", preset]
        else:
            vcodec_opts = ["-c:v", encoder, "-b:v", bitrate_str, "-preset", "medium"]

        null_dev = "NUL" if os.name == 'nt' else "/dev/null"
        return [self.ffmpeg_path, "-y", "-i", task.input_file] + vcodec_opts + ["-pass", "1", "-an", "-f", "null", null_dev]

    def _build_pass2_cmd(self, task, video_bitrate):
        """构建2-pass第二遍（正式编码）命令"""
        p = task.params
        encoder = p.get('video_encoder', 'libx264')
        bitrate_str = f"{video_bitrate // 1000}k"

        cmd = [self.ffmpeg_path, "-y", "-i", task.input_file]

        if "nvenc" in encoder:
            preset = "p4" if "av1" in encoder else "p2"
            cmd.extend(["-c:v", encoder, "-b:v", bitrate_str, "-preset", preset])
        else:
            cmd.extend(["-c:v", encoder, "-b:v", bitrate_str, "-preset", "medium"])

        cmd.extend(["-pass", "2"])

        resolution = p.get("resolution")
        if resolution and resolution not in ("original", "", None):
            cmd.extend(["-vf", f"scale={resolution}"])

        audio_bitrate = p.get("audio_bitrate", "128k")
        if audio_bitrate == "copy":
            audio_bitrate = "128k"
        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate])

        cmd.append(task.output_file)
        return cmd

    def _clean_pass_logs(self):
        """清理2-pass产生的日志文件"""
        for f in ['ffmpeg2pass-0.log', 'ffmpeg2pass-0.log.mbtree']:
            if os.path.exists(f):
                try:
                    os.unlink(f)
                except OSError:
                    pass

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
                # h264/hevc: p2 速度快质量好; av1: p4 平衡
                preset = "p4" if "av1" in video_encoder else "p2"
                cmd.extend(["-c:v", video_encoder, "-cq", str(crf), "-preset", preset])
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
        """取消任务：pending 直接标记取消，running 两步终止（terminate → kill）"""
        task = self.tasks.get(task_id)
        if not task:
            return False

        # pending 任务直接标记为 cancelled
        if task.status == "pending":
            task._cancelled = True
            task.status = "cancelled"
            task.phase = "cancelled"
            return True

        # running 任务：先设标志（编码循环会检查），然后两步终止进程
        if task.process and task.process.poll() is None:
            task._cancelled = True
            task.process.terminate()
            time.sleep(0.5)
            if task.process.poll() is None:
                try:
                    task.process.kill()
                except Exception:
                    pass
            task.status = "cancelled"
            task.phase = "cancelled"
            return True

        return False

    def get_task(self, task_id):
        return self.tasks.get(task_id)
