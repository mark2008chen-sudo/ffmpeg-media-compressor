"""
FFmpeg Media Compressor - Flask 主服务器
"""
import os
import sys
import json
import uuid
import shutil
import threading
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template, Response, stream_with_context

from .ffmpeg_manager import (
    check_installed, get_available_encoders, get_media_info,
    download_and_install_ffmpeg, BIN_DIR, detect_hardware,
)
from .size_predictor import predict_size
from .compression_engine import CompressionEngine, CompressionTask

BASE_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = BASE_DIR / "temp"
UPLOAD_DIR = TEMP_DIR / "uploads"
OUTPUT_DIR = TEMP_DIR / "outputs"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates" if (BASE_DIR / "templates").exists() else BASE_DIR / "backend" / "templates"),
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)

# 全局状态
ffmpeg_info = {"path": None, "version": None, "installed": False}
ffprobe_path = None
engine = None
_install_status = {"progress": "", "done": False, "success": False, "error": None}
max_concurrent_tasks = 2  # 默认同时最多2个任务


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """获取系统状态：FFmpeg安装状态、可用编码器"""
    global ffmpeg_info, ffprobe_path, engine
    installed, info = check_installed()
    # Debug: 打印检测结果到日志
    print(f"[DEBUG] check_installed result: installed={installed}, info={info}")
    if installed:
        ffmpeg_info["path"] = info[0]
        ffmpeg_info["version"] = info[1]
        ffmpeg_info["installed"] = True

        # 获取ffprobe路径
        from .ffmpeg_manager import _get_ffprobe_path
        ffprobe_path = _get_ffprobe_path()
        if not ffprobe_path:
            ffprobe_path = str(BIN_DIR / "ffprobe.exe") if os.name == "nt" else str(BIN_DIR / "ffprobe")

        if engine is None:
            engine = CompressionEngine(ffmpeg_info["path"], ffprobe_path)

        hw_info = detect_hardware()
        print(f"[DEBUG] detect_hardware: {json.dumps(hw_info, ensure_ascii=False, default=str)}")
        encoders = get_available_encoders(ffmpeg_info["path"], hw_info)
        print(f"[DEBUG] encoders: {[e['id'] + ('(推荐)' if e.get('recommended') else '') for e in encoders]}")
    else:
        encoders = []
        hw_info = None

    return jsonify({
        "ffmpeg_installed": ffmpeg_info["installed"],
        "ffmpeg_version": ffmpeg_info["version"],
        "ffmpeg_path": ffmpeg_info["path"],
        "encoders": encoders,
        "hardware": hw_info,
    })


@app.route("/api/install/start")
def install_start():
    """启动FFmpeg自动安装"""
    global _install_status
    _install_status = {"progress": "", "done": False, "success": False, "error": None}

    def install_thread():
        global _install_status, ffmpeg_info
        def cb(msg):
            _install_status["progress"] = msg
        success, path, err = download_and_install_ffmpeg(cb)
        _install_status["success"] = success
        _install_status["error"] = err
        _install_status["done"] = True

    thread = threading.Thread(target=install_thread, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/install/progress")
def install_progress():
    """SSE流式返回安装进度"""
    def generate():
        global _install_status
        while not _install_status["done"]:
            yield f"data: {json.dumps({'progress': _install_status['progress'], 'done': False})}\n\n"
            import time
            time.sleep(0.5)

        yield f"data: {json.dumps({
            'progress': _install_status['progress'],
            'done': True,
            'success': _install_status['success'],
            'error': _install_status['error'],
        })}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/upload", methods=["POST"])
def upload_file():
    """上传文件"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "未选择文件"}), 400

    uploaded = []
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f.filename:
            safe_name = f"{uuid.uuid4().hex}_{f.filename}"
            save_path = UPLOAD_DIR / safe_name
            f.save(str(save_path))
            size = os.path.getsize(str(save_path))
            uploaded.append({
                "id": safe_name,
                "name": f.filename,
                "path": str(save_path),
                "size": size,
                "size_mb": round(size / 1048576, 1),
            })

    return jsonify({"files": uploaded})


@app.route("/api/delete_upload", methods=["POST"])
def delete_upload():
    """删除已上传的文件"""
    data = request.get_json()
    file_path = data.get("path", "")
    if os.path.exists(file_path) and file_path.startswith(str(UPLOAD_DIR)):
        os.remove(file_path)
        return jsonify({"success": True})
    return jsonify({"error": "文件不存在"}), 404


@app.route("/api/media_info", methods=["POST"])
def media_info():
    """获取媒体文件信息"""
    data = request.get_json()
    file_path = data.get("path", "")
    if not os.path.exists(file_path):
        return jsonify({"error": "文件不存在"}), 404

    info = get_media_info(ffprobe_path, file_path)
    if info:
        return jsonify({"info": json.loads(info)})
    return jsonify({"error": "无法读取文件信息"}), 400


@app.route("/api/predict", methods=["POST"])
def predict():
    """预测压缩后文件大小"""
    data = request.get_json()
    file_path = data.get("path", "")

    if not ffmpeg_info["path"] or not ffprobe_path:
        return jsonify({"error": "FFmpeg 未安装"}), 400
    if not os.path.exists(file_path):
        return jsonify({"error": "文件不存在"}), 404

    params = data.get("params", {})
    try:
        result = predict_size(ffmpeg_info["path"], ffprobe_path, file_path, params)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"预测失败: {str(e)}"}), 500


@app.route("/api/compress", methods=["POST"])
def compress():
    """开始压缩任务"""
    global engine
    if not engine:
        return jsonify({"error": "压缩引擎未初始化"}), 400

    data = request.get_json()
    files = data.get("files", [])
    params = data.get("params", {})

    if not files:
        return jsonify({"error": "未选择文件"}), 400

    tasks = []
    for f in files:
        input_path = f.get("path", "")
        if not os.path.exists(input_path):
            continue

        tid = uuid.uuid4().hex
        orig_name = Path(input_path).name
        # 去掉uuid前缀
        clean_name = orig_name.split("_", 1)[-1] if "_" in orig_name else orig_name
        name_stem = Path(clean_name).stem

        out_ext = params.get("output_format", "mp4")
        output_name = f"{name_stem}_compressed.{out_ext}"
        output_path = str(OUTPUT_DIR / output_name)

        task = CompressionTask(tid, input_path, output_path, params)
        engine.start_task(task)
        tasks.append(task.to_dict())

    return jsonify({"tasks": tasks})


@app.route("/api/task/<task_id>")
def get_task(task_id):
    """获取任务状态"""
    if not engine:
        return jsonify({"error": "引擎未初始化"}), 400
    task = engine.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task.to_dict())


@app.route("/api/tasks")
def list_tasks():
    """列出所有任务"""
    if not engine:
        return jsonify({"tasks": []})
    return jsonify({"tasks": [t.to_dict() for t in engine.tasks.values()]})


@app.route("/api/hardware_info")
def hardware_info():
    """获取硬件信息（GPU、CPU）"""
    try:
        hw = detect_hardware()
        return jsonify(hw)
    except Exception as e:
        return jsonify({"error": str(e), "gpus": [], "cpu": {"model": "Unknown"}, "recommended_encoder": None})


@app.route("/api/cancel/<task_id>", methods=["POST"])
def cancel_task(task_id):
    """取消任务"""
    if not engine:
        return jsonify({"success": True})
    engine.cancel_task(task_id)
    return jsonify({"success": True})


@app.route("/api/cancel_all", methods=["POST"])
def cancel_all_tasks():
    """取消所有运行中的任务"""
    if not engine:
        return jsonify({"success": True, "cancelled": []})
    cancelled = []
    for tid, task in list(engine.tasks.items()):
        if task.status == "running" or task.status == "pending":
            engine.cancel_task(tid)
            cancelled.append(tid)
    return jsonify({"success": True, "cancelled": cancelled})


@app.route("/api/config", methods=["POST"])
def update_config():
    """更新配置（并行任务数等）"""
    global max_concurrent_tasks
    data = request.get_json()
    if "max_concurrent" in data:
        max_concurrent_tasks = max(1, min(int(data["max_concurrent"]), 8))
    return jsonify({"max_concurrent": max_concurrent_tasks})


@app.route("/api/config", methods=["GET"])
def get_config():
    """获取当前配置"""
    return jsonify({"max_concurrent": max_concurrent_tasks})


@app.route("/api/download/<filename>")
def download_file(filename):
    """下载压缩后的文件"""
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        return jsonify({"error": "文件不存在"}), 404
    return send_file(str(file_path), as_attachment=True, download_name=filename)


@app.route("/api/preview/<filename>")
def preview_file(filename):
    """预览压缩后的文件（内嵌播放）"""
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        return jsonify({"error": "文件不存在"}), 404
    ext = Path(filename).suffix.lower()
    mime_map = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                ".mkv": "video/x-matroska", ".mp3": "audio/mpeg", ".aac": "audio/aac",
                ".wav": "audio/wav", ".flac": "audio/flac"}
    mt = mime_map.get(ext, "application/octet-stream")
    return send_file(str(file_path), mimetype=mt)


@app.route("/api/report")
def generate_report():
    """生成所有已完成任务的 HTML 压缩报告"""
    if not engine:
        return jsonify({"error": "引擎未初始化"}), 400
    done_tasks = [t.to_dict() for t in engine.tasks.values() if t.status == "completed"]
    if not done_tasks:
        return jsonify({"error": "没有已完成的任务"}), 404

    def _format_size(bytes_val):
        if not bytes_val:
            return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} TB"

    rows = ""
    for t in done_tasks:
        orig = t.get("original_size", 0)
        comp = t.get("output_size", 0)
        savings = f"{(1 - comp/orig)*100:.1f}%" if orig and comp else "--"
        rows += f"""<tr>
            <td>{t['input_file']}</td>
            <td>{_format_size(orig)}</td>
            <td>{_format_size(comp)}</td>
            <td>{savings}</td>
            <td>CRF {t.get('progress', '--')}</td>
            <td>{t.get('elapsed', 0)}秒</td>
            <td><a href="/api/download/{t['output_file']}">下载</a></td>
        </tr>\n"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>压缩报告</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 960px; margin: 0 auto; padding: 20px; }}
h1 {{ color: #1e293b; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
th {{ background: #f8fafc; font-weight: 600; }}
tr:hover {{ background: #f1f5f9; }}
.status-ok {{ color: #16a34a; }}
</style></head>
<body>
<h1>FFmpeg 压缩报告</h1>
<p>生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>共 {len(done_tasks)} 个文件</p>
<table>
<thead><tr>
<th>文件名</th><th>原始大小</th><th>压缩后</th><th>节省</th><th>参数</th><th>耗时</th><th>操作</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""
    return Response(html, mimetype="text/html")


@app.route("/api/clear_temp", methods=["POST"])
def clear_temp():
    """清理临时文件"""
    for d in [UPLOAD_DIR, OUTPUT_DIR]:
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()
        d.mkdir(parents=True, exist_ok=True)
    return jsonify({"success": True})


def create_app():
    """创建并初始化应用"""
    for d in [UPLOAD_DIR, OUTPUT_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    return app


# 模块加载时确保目录存在
for d in [UPLOAD_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # Fix Windows GBK encoding issue for emoji output
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    create_app()
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  🎬 FFmpeg 媒体压缩工具 v1.0")
    print(f"  ─────────────────────────────")
    print(f"  🌐 打开浏览器访问: http://localhost:{port}")
    print(f"  📁 输出目录: {OUTPUT_DIR}")
    print(f"  ─────────────────────────────\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
