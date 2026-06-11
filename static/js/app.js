// FFmpeg Media Compressor - Frontend App
(() => {
    'use strict';

    const API = {
        status: '/api/status',
        installStart: '/api/install/start',
        installProgress: '/api/install/progress',
        upload: '/api/upload',
        deleteUpload: '/api/delete_upload',
        mediaInfo: '/api/media_info',
        predict: '/api/predict',
        compress: '/api/compress',
        task: '/api/task/',
        tasks: '/api/tasks',
        cancel: '/api/cancel/',
        download: '/api/download/',
        clearTemp: '/api/clear_temp',
    };

    let uploadedFiles = [];
    let taskIds = [];
    let pollInterval = null;

    // ============ Init ============
    document.addEventListener('DOMContentLoaded', () => {
        initDropZone();
        initFileInput();
        checkStatus();
    });

    // ============ FFmpeg Status ============
    async function checkStatus() {
        const statusEl = document.getElementById('ffmpegStatus');
        try {
            const res = await fetch(API.status);
            const data = await res.json();

            if (data.ffmpeg_installed) {
                statusEl.textContent = `✅ FFmpeg ${data.ffmpeg_version || '已安装'}`;
                statusEl.className = 'status-tag status-ok';
                document.getElementById('mainUI').classList.remove('hidden');
                document.getElementById('installWizard').classList.add('hidden');
                populateEncoders(data.encoders);
            } else {
                statusEl.textContent = '❌ 未安装 FFmpeg';
                statusEl.className = 'status-tag status-error';
                document.getElementById('installWizard').classList.remove('hidden');
                document.getElementById('mainUI').classList.add('hidden');
            }
        } catch (e) {
            statusEl.textContent = '❌ 连接失败';
            statusEl.className = 'status-tag status-error';
        }
    }

    function populateEncoders(encoders) {
        const sel = document.getElementById('encoderSelect');
        sel.innerHTML = '';
        encoders.forEach(enc => {
            const opt = document.createElement('option');
            opt.value = enc.id;
            opt.textContent = enc.name;
            if (enc.type === 'gpu') {
                opt.textContent += ' 🚀 (GPU)';
            }
            sel.appendChild(opt);
        });
    }

    // ============ FFmpeg Install ============
    window.startInstall = function() {
        const btn = document.getElementById('installBtn');
        const progress = document.getElementById('installProgress');
        const bar = document.getElementById('installBar');
        const msg = document.getElementById('installMsg');

        btn.disabled = true;
        btn.textContent = '⏳ 安装中...';
        progress.classList.remove('hidden');

        fetch(API.installStart).then(() => {
            const evtSource = new EventSource(API.installProgress);
            evtSource.onmessage = (e) => {
                const data = JSON.parse(e.data);
                msg.textContent = data.progress;
                bar.style.width = data.done ? '100%' : Math.min(bar.offsetWidth / 400 * 100 + 5, 90) + '%';

                if (data.done) {
                    evtSource.close();
                    if (data.success) {
                        msg.textContent = '✅ FFmpeg 安装成功！';
                        bar.style.width = '100%';
                        setTimeout(() => checkStatus(), 1000);
                    } else {
                        msg.textContent = '❌ 安装失败：' + (data.error || '未知错误');
                        btn.disabled = false;
                        btn.textContent = '🔄 重试安装';
                    }
                }
            };
        });
    };

    // ============ File Upload ============
    function initDropZone() {
        const dz = document.getElementById('dropZone');
        dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
        dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
        dz.addEventListener('drop', (e) => {
            e.preventDefault();
            dz.classList.remove('drag-over');
            if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
        });
    }

    function initFileInput() {
        document.getElementById('fileInput').addEventListener('change', (e) => {
            if (e.target.files.length) uploadFiles(e.target.files);
        });
    }

    function uploadFiles(files) {
        const formData = new FormData();
        for (const f of files) formData.append('files', f);

        fetch(API.upload, { method: 'POST', body: formData })
            .then(r => r.json())
            .then(data => {
                if (data.files) {
                    uploadedFiles.push(...data.files);
                    renderFileList();
                }
            })
            .catch(e => alert('上传失败：' + e.message));
    }

    function renderFileList() {
        const el = document.getElementById('fileList');
        el.innerHTML = uploadedFiles.map((f, i) => `
            <div class="file-item">
                <div>
                    <span class="file-name">${f.name}</span>
                    <span class="file-size">${formatSize(f.size)}</span>
                </div>
                <span class="file-remove" onclick="removeFile(${i})">✕</span>
            </div>
        `).join('');

        document.getElementById('compressBtn').disabled = uploadedFiles.length === 0;
        document.getElementById('predictBtn').disabled = uploadedFiles.length === 0;
    }

    window.removeFile = function(idx) {
        const f = uploadedFiles[idx];
        if (f && f.path) {
            fetch(API.deleteUpload, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path: f.path}) });
        }
        uploadedFiles.splice(idx, 1);
        renderFileList();
        document.getElementById('predictResult').classList.add('hidden');
    };

    // ============ Mode Change ============
    window.onModeChange = function() {
        const mode = document.getElementById('compressionMode').value;
        const slider = document.getElementById('crfSlider');
        const label = document.getElementById('crfValue');
        if (mode === 'quality') { slider.value = 28; label.textContent = '28'; }
        else if (mode === 'balanced') { slider.value = 23; label.textContent = '23'; }
        else if (mode === 'small') { slider.value = 18; label.textContent = '18'; }
    };

    // ============ Size Predict ============
    window.predictSize = async function() {
        if (!uploadedFiles.length) return;

        const btn = document.getElementById('predictBtn');
        const result = document.getElementById('predictResult');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> 预测中...';
        result.classList.add('hidden');

        const params = getCompressParams();
        let totalOrig = 0, totalPred = 0, totalConf = 0;

        for (const f of uploadedFiles) {
            try {
                const res = await fetch(API.predict, {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({path: f.path, params}),
                });
                const data = await res.json();
                if (data.error) continue;
                totalOrig += data.original_size_mb;
                totalPred += data.predicted_size_mb;
                totalConf += data.confidence;
            } catch(e) { continue; }
        }

        if (totalOrig > 0) {
            const savings = ((1 - totalPred / totalOrig) * 100).toFixed(1);
            const conf = (totalConf / uploadedFiles.length * 100).toFixed(0);
            document.getElementById('predOrigSize').textContent = totalOrig.toFixed(1) + ' MB';
            document.getElementById('predCompSize').textContent = totalPred.toFixed(1) + ' MB';
            document.getElementById('predSavings').textContent = savings + '%';
            document.getElementById('predBar').style.width = Math.min(100, parseFloat(savings)) + '%';
            document.getElementById('predConfidence').textContent = `置信度：${conf}%`;

            result.classList.remove('hidden');
        }

        btn.disabled = false;
        btn.innerHTML = '📈 预测大小';
    };

    function getCompressParams() {
        return {
            video_encoder: document.getElementById('encoderSelect').value,
            crf: parseInt(document.getElementById('crfSlider').value),
            audio_encoder: 'aac',
            audio_bitrate: document.getElementById('audioBitrate').value,
            output_format: document.getElementById('outputFormat').value,
            resolution: document.getElementById('resolution').value,
        };
    }

    // ============ Compress ============
    window.startCompress = async function() {
        if (!uploadedFiles.length) return;

        const params = getCompressParams();
        const btn = document.getElementById('compressBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> 压缩中...';

        try {
            const res = await fetch(API.compress, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({files: uploadedFiles, params}),
            });
            const data = await res.json();
            if (data.tasks) {
                taskIds.push(...data.tasks.map(t => t.task_id));
                startPolling();
            }
        } catch(e) {
            alert('启动压缩失败：' + e.message);
            btn.disabled = false;
            btn.innerHTML = '🚀 开始压缩';
        }
    };

    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(pollTasks, 1000);
        pollTasks();
    }

    async function pollTasks() {
        try {
            const res = await fetch(API.tasks);
            const data = await res.json();
            renderProgress(data.tasks);
            renderResults(data.tasks);

            const allDone = data.tasks.length > 0 && data.tasks.every(t => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled');
            if (allDone) {
                clearInterval(pollInterval);
                pollInterval = null;
                document.getElementById('compressBtn').disabled = false;
                document.getElementById('compressBtn').innerHTML = '🚀 开始压缩';
            }
        } catch(e) { /* ignore */ }
    }

    function renderProgress(tasks) {
        const body = document.getElementById('progressBody');
        const active = tasks.filter(t => t.status !== 'completed' && t.status !== 'failed' && t.status !== 'cancelled');

        if (!active.length && tasks.every(t => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled')) {
            // All done - show summary
            body.innerHTML = active.length === 0 && tasks.length > 0 ? '' : body.innerHTML;
            return;
        }

        body.innerHTML = active.map(t => {
            const statusText = t.status === 'running' ? '压缩中...' : '等待中';
            const fillClass = t.status === 'failed' ? 'failed' : '';
            return `
                <div class="task-card">
                    <div class="task-header">
                        <span class="task-name">${t.input_file}</span>
                        <span class="task-status">${statusText}</span>
                    </div>
                    <div class="task-extra">
                        <span>⏱ ${t.elapsed || 0}秒</span>
                        <span>📦 ${t.output_size ? formatSize(t.output_size) : '--'}</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-fill ${fillClass}" style="width:${t.progress}%"></div>
                    </div>
                </div>
            `;
        }).join('');
    }

    function renderResults(tasks) {
        const done = tasks.filter(t => t.status === 'completed');
        const body = document.getElementById('resultBody');

        if (!done.length) {
            body.innerHTML = '<p class="text-muted">暂无完成文件</p>';
            return;
        }

        body.innerHTML = done.map(t => {
            const savings = t.output_size ? ((1 - t.output_size / getOriginalSize(t.input_file)) * 100).toFixed(1) : '--';
            return `
                <div class="result-item">
                    <div>
                        <span class="result-name">${t.output_file}</span>
                        <span class="result-size">${t.output_size ? formatSize(t.output_size) : '--'}</span>
                        <span class="result-savings">(节省 ${savings}%)</span>
                    </div>
                    <button class="download-btn" onclick="downloadFile('${t.output_file}')">下载</button>
                </div>
            `;
        }).join('');

        // 添加全部下载按钮
        if (done.length > 1) {
            const allBtn = document.createElement('div');
            allBtn.style.padding = '10px 12px';
            allBtn.innerHTML = '<button class="btn btn-primary" onclick="downloadAll()">📦 下载全部</button>';
            // Only add if not already there
            if (!body.querySelector('.download-all-btn')) {
                const wrapper = document.createElement('div');
                wrapper.className = 'download-all-btn';
                wrapper.style.padding = '10px 12px';
                wrapper.innerHTML = '<button class="btn btn-primary" onclick="downloadAll()">📦 下载全部</button>';
                body.appendChild(wrapper);
            }
        }
    }

    function getOriginalSize(filename) {
        const file = uploadedFiles.find(f => f.name === filename || f.path.endsWith(filename));
        return file ? file.size : 0;
    }

    window.downloadFile = function(filename) {
        window.open(API.download + encodeURIComponent(filename));
    };

    window.downloadAll = function() {
        const done = taskIds.map(id => {
            // We need to know which tasks are completed
        });
        // Simple approach: open each download link
        fetch(API.tasks).then(r => r.json()).then(data => {
            data.tasks.filter(t => t.status === 'completed').forEach(t => {
                window.open(API.download + encodeURIComponent(t.output_file));
            });
        });
    };

    // ============ Clear ============
    window.clearAll = function() {
        uploadedFiles.forEach(f => {
            if (f.path) fetch(API.deleteUpload, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path: f.path}) });
        });
        uploadedFiles = [];
        taskIds = [];
        renderFileList();
        document.getElementById('progressBody').innerHTML = '<p class="text-muted">暂无任务</p>';
        document.getElementById('resultBody').innerHTML = '<p class="text-muted">暂无完成文件</p>';
        document.getElementById('predictResult').classList.add('hidden');
        fetch(API.clearTemp, { method: 'POST' });
    };

    // ============ Utils ============
    function formatSize(bytes) {
        if (!bytes) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let i = 0;
        let size = bytes;
        while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
        return size.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
    }
})();
