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
        cancelAll: '/api/cancel_all',
        config: '/api/config',
        download: '/api/download/',
        preview: '/api/preview/',
        report: '/api/report',
        clearTemp: '/api/clear_temp',
        hardware: '/api/hardware_info',
    };

    let uploadedFiles = [];
    let taskIds = [];
    let pollInterval = null;
    let _activeTargetPct = 60;
    let _notificationEnabled = false;

    const STORAGE_KEY = 'ffmpeg_compressor_settings';

    // ============ Init ============
    document.addEventListener('DOMContentLoaded', () => {
        initDropZone();
        initFileInput();
        checkStatus();
        initPresets();
        loadSettings();
        initBeforeUnload();
        initNotification();
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
                if (data.hardware) updateHardwareInfo(data.hardware);
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
        let hasRecommended = false;
        let recommendedId = null;

        encoders.forEach(enc => {
            const opt = document.createElement('option');
            opt.value = enc.id;
            let text = enc.name;
            if (enc.type === 'gpu') {
                text += ' 🚀 (GPU)';
            }
            if (enc.recommended) {
                text += ' [推荐]';
                recommendedId = enc.id;
                hasRecommended = true;
            }
            opt.textContent = text;
            sel.appendChild(opt);
        });

        // 自动选中推荐的 GPU 编码器
        if (hasRecommended && recommendedId) {
            sel.value = recommendedId;
            saveSettings();
        } else if (sel.options.length === 0) {
            // 没有编码器时默认选 CPU
            sel.value = sel.options[0]?.value || '';
        }
    }

    // 在 checkStatus 成功后显示硬件信息
    function updateHardwareInfo(hardware) {
        if (!hardware || !hardware.gpus || !hardware.gpus.length) return;

        const gpuNames = hardware.gpus.map(g => g.name).join('、');
        const hintEl = document.getElementById('encoderSelect').parentElement.querySelector('.form-hint');
        if (hintEl) {
            hintEl.textContent = `检测到 GPU: ${gpuNames}；CPU: ${hardware.cpu.model}（${hardware.cpu.cores_logical} 线程）`;
        }
    }

    // ============ Settings Persistence ============
    function saveSettings() {
        try {
            const data = {
                encoder: document.getElementById('encoderSelect').value,
                outputFormat: document.getElementById('outputFormat').value,
                crf: document.getElementById('crfSlider').value,
                audioBitrate: document.getElementById('audioBitrate').value,
                resolution: document.getElementById('resolution').value,
                compressionMode: document.getElementById('compressionMode').value,
                maxConcurrent: document.getElementById('maxConcurrent').value,
                targetPercent: _activeTargetPct,
                darkMode: document.documentElement.getAttribute('data-theme') === 'dark',
            };
            localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
        } catch(e) { /* ignore */ }
    }

    function loadSettings() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            const data = JSON.parse(raw);
            if (data.encoder) {
                const sel = document.getElementById('encoderSelect');
                if ([...sel.options].some(o => o.value === data.encoder)) sel.value = data.encoder;
            }
            if (data.outputFormat) document.getElementById('outputFormat').value = data.outputFormat;
            if (data.crf) {
                document.getElementById('crfSlider').value = data.crf;
                document.getElementById('crfValue').textContent = data.crf;
            }
            if (data.audioBitrate) document.getElementById('audioBitrate').value = data.audioBitrate;
            if (data.resolution) document.getElementById('resolution').value = data.resolution;
            if (data.compressionMode) {
                document.getElementById('compressionMode').value = data.compressionMode;
            }
            if (data.maxConcurrent) document.getElementById('maxConcurrent').value = data.maxConcurrent;
            if (data.targetPercent) _activeTargetPct = data.targetPercent;
            if (data.darkMode) {
                document.documentElement.setAttribute('data-theme', 'dark');
                document.getElementById('darkModeToggle').textContent = '☀️';
            }
        } catch(e) { /* ignore */ }
    }

    // Auto-save on setting changes
    ['encoderSelect', 'outputFormat', 'crfSlider', 'audioBitrate', 'resolution', 'compressionMode', 'maxConcurrent'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', saveSettings);
    });

    // ============ Window Close Handling ============
    function initBeforeUnload() {
        // Stop all tasks when page is closed/hidden
        document.addEventListener('visibilitychange', () => {
            if (document.hidden && taskIds.length > 0) {
                fetch(API.cancelAll, { method: 'POST', keepalive: true }).catch(() => {});
            }
        });
        window.addEventListener('beforeunload', (e) => {
            if (taskIds.length > 0) {
                // fetch keepalive 比 sendBeacon 更可靠（支持自定义 header 和 content-type）
                fetch(API.cancelAll, { method: 'POST', keepalive: true }).catch(() => {});
            }
        });
    }

    // ============ Desktop Notification ============
    function initNotification() {
        if ('Notification' in window && Notification.permission === 'granted') {
            _notificationEnabled = true;
        }
    }

    function requestNotificationPermission() {
        if (!('Notification' in window)) return;
        if (Notification.permission === 'granted') {
            _notificationEnabled = true;
        } else if (Notification.permission === 'default') {
            Notification.requestPermission().then(perm => {
                _notificationEnabled = perm === 'granted';
            });
        }
    }

    function sendNotification(title, body) {
        if (!_notificationEnabled) return;
        if (!document.getElementById('autoNotifyCheck').checked) return;
        try {
            new Notification(title, { body, icon: '🎬' });
        } catch(e) { /* ignore */ }
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

    let uploadTaskProgress = {};  // fileName → {loaded, total, pct}

    function uploadFiles(files) {
        const fileArray = Array.from(files);
        if (!fileArray.length) return;

        // 初始化上传进度容器
        const progressContainer = initUploadProgressContainer();
        uploadTaskProgress = {};

        let completed = 0;
        function uploadNext(index) {
            if (index >= fileArray.length) {
                // 全部完成，延迟隐藏进度容器
                setTimeout(() => {
                    const container = document.getElementById('uploadProgressList');
                    if (container) container.innerHTML = '';
                    document.getElementById('uploadProgressCard').classList.add('hidden');
                }, 1500);
                return;
            }

            const file = fileArray[index];
            const formData = new FormData();
            formData.append('files', file);

            const xhr = new XMLHttpRequest();
            xhr.open('POST', API.upload, true);

            // 上传进度
            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) {
                    const pct = Math.round(e.loaded / e.total * 100);
                    uploadTaskProgress[file.name] = { loaded: e.loaded, total: e.total, pct };
                    renderUploadProgress(fileArray, index, completed);
                }
            };

            xhr.onload = () => {
                if (xhr.status === 200) {
                    try {
                        const data = JSON.parse(xhr.responseText);
                        if (data.files) {
                            uploadedFiles.push(...data.files);
                            renderFileList();
                        }
                    } catch (e) { /* ignore */ }
                }
                completed++;
                uploadTaskProgress[file.name] = { loaded: uploadTaskProgress[file.name]?.total || file.size, total: file.size, pct: 100 };
                renderUploadProgress(fileArray, index, completed);
                uploadNext(index + 1);
            };

            xhr.onerror = () => {
                completed++;
                uploadTaskProgress[file.name] = { loaded: 0, total: file.size, pct: -1 }; // -1 表示失败
                renderUploadProgress(fileArray, index, completed);
                uploadNext(index + 1);
            };

            xhr.send(formData);
        }

        uploadNext(0);
    }

    function initUploadProgressContainer() {
        let card = document.getElementById('uploadProgressCard');
        if (!card) {
            card = document.createElement('div');
            card.id = 'uploadProgressCard';
            card.className = 'card hidden';
            card.innerHTML = `
                <div class="card-header">📤 上传进度</div>
                <div class="card-body" id="uploadProgressList"></div>
            `;
            document.getElementById('dropZone').parentNode.insertBefore(card, document.getElementById('dropZone').nextSibling);
        }
        card.classList.remove('hidden');
        return card;
    }

    function renderUploadProgress(fileArray, currentIndex, completed) {
        const container = document.getElementById('uploadProgressList');
        if (!container) return;

        let html = '';
        fileArray.forEach((file, i) => {
            const info = uploadTaskProgress[file.name] || { loaded: 0, total: file.size, pct: 0 };
            const failed = info.pct === -1;
            const done = info.pct === 100;
            const active = i === currentIndex && !done && !failed;

            html += `
                <div class="upload-item">
                    <div class="upload-item-header">
                        <span class="upload-name">${file.name}</span>
                        <span class="upload-status">
                            ${failed ? '❌ 失败' : done ? '✅ 完成' : active ? '⏳ 上传中...' : '⏸ 等待'}
                        </span>
                    </div>
                    <div class="upload-progress-row">
                        <span class="upload-pct">${failed ? '--' : info.pct + '%'}</span>
                        <div class="progress-track" style="flex:1">
                            <div class="progress-fill upload-fill ${failed ? 'failed' : ''}" style="width:${failed ? 0 : info.pct}%"></div>
                        </div>
                    </div>
                    ${active ? `<div class="upload-size-info">${formatSize(info.loaded)} / ${formatSize(info.total)}</div>` : ''}
                </div>
            `;
        });

        html += `<div class="upload-summary">${completed} / ${fileArray.length} 个文件完成</div>`;
        container.innerHTML = html;
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
        // 如果复选框被勾选，自动取消勾选并隐藏百分比按钮
        const toggle = document.getElementById('targetSizeToggle');
        if (toggle.checked) {
            toggle.checked = false;
            document.getElementById('targetSizeOptions').classList.add('hidden');
            lockTargetControls(false);
            document.getElementById('targetModeNote').classList.add('hidden');
        }
        // 恢复简易选项预设
        deselectPreset();
        const slider = document.getElementById('crfSlider');
        const label = document.getElementById('crfValue');
        if (mode === 'quality') { slider.value = 28; label.textContent = '28'; }
        else if (mode === 'balanced') { slider.value = 23; label.textContent = '23'; }
        else if (mode === 'small') { slider.value = 18; label.textContent = '18'; }
        selectPreset(mode === 'quality' ? 'quality' : mode === 'small' ? 'small' : 'balanced');
    };

    // ============ Target Size Toggle (复选框) ============
    window.onTargetSizeToggle = function() {
        const checked = document.getElementById('targetSizeToggle').checked;
        const targetOpts = document.getElementById('targetSizeOptions');
        const presetGroup = document.getElementById('presetGroup');

        if (checked) {
            // 勾选 → 隐藏简易选项，显示百分比滑块，进入 target_size 模式
            deselectPreset();
            presetGroup.classList.add('hidden');
            targetOpts.classList.remove('hidden');
            lockTargetControls(true);
            document.getElementById('targetModeNote').classList.remove('hidden');
            // 初始化滑块值
            const slider = document.getElementById('targetPctSlider');
            const display = document.getElementById('targetPctValue');
            slider.value = _activeTargetPct || 60;
            display.textContent = (_activeTargetPct || 60) + '%';
        } else {
            // 取消勾选 → 隐藏百分比按钮，恢复简易选项
            targetOpts.classList.add('hidden');
            presetGroup.classList.remove('hidden');
            lockTargetControls(false);
            document.getElementById('targetModeNote').classList.add('hidden');
            // 恢复默认 balanced 预设
            selectPreset('balanced');
            document.getElementById('compressionMode').value = 'balanced';
        }
    };

    // ============ Target Size (2-pass) ============
    window.onTargetPctChange = function() {
        const slider = document.getElementById('targetPctSlider');
        const display = document.getElementById('targetPctValue');
        _activeTargetPct = parseInt(slider.value);
        display.textContent = _activeTargetPct + '%';
    };

    function lockTargetControls(disabled) {
        const ids = ['crfSlider', 'audioBitrate', 'resolution'];
        ids.forEach(id => {
            const el = document.getElementById(id);
            el.disabled = disabled;
            el.classList.toggle('control-disabled', disabled);
        });
    }

    // ============ Simple Presets ============
    let _activePreset = 'balanced';
    let _presetUpdating = false;

    const PRESET_MAP = {
        quality: { crf: 20, audio_bitrate: 'copy', resolution: 'original', label: '保存现画质' },
        balanced: { crf: 23, audio_bitrate: '192k', resolution: 'original', label: '中等画质' },
        small: { crf: 28, audio_bitrate: '128k', resolution: '1280x720', label: '低画质' },
    };

    window.selectPreset = function(preset) {
        _activePreset = preset;
        _presetUpdating = true;

        // Update button UI
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.classList.toggle('preset-active', btn.dataset.preset === preset);
        });

        // Apply preset parameters to controls
        const p = PRESET_MAP[preset];
        const slider = document.getElementById('crfSlider');
        document.getElementById('crfValue').textContent = p.crf;
        slider.value = p.crf;
        document.getElementById('audioBitrate').value = p.audio_bitrate;
        document.getElementById('resolution').value = p.resolution;

        _presetUpdating = false;

        // Disable advanced controls with visual feedback
        setAdvancedControlsDisabled(true);
    };

    function deselectPreset() {
        if (!_activePreset) return;
        _activePreset = null;
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.classList.remove('preset-active');
        });
        setAdvancedControlsDisabled(false);
    }

    function setAdvancedControlsDisabled(disabled) {
        const slider = document.getElementById('crfSlider');
        const audioBitrate = document.getElementById('audioBitrate');
        const resolution = document.getElementById('resolution');
        const note = document.getElementById('presetNote');

        slider.disabled = disabled;
        audioBitrate.disabled = disabled;
        resolution.disabled = disabled;

        slider.classList.toggle('control-disabled', disabled);
        audioBitrate.classList.toggle('control-disabled', disabled);
        resolution.classList.toggle('control-disabled', disabled);

        if (note) note.classList.toggle('hidden', !disabled);
    }

    function onAdvancedManualChange() {
        if (_presetUpdating) return;
        deselectPreset();
    }

    function initPresets() {
        // Watch for manual changes on preset-controlled controls
        ['crfSlider', 'audioBitrate', 'resolution'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', onAdvancedManualChange);
                el.addEventListener('input', onAdvancedManualChange);
            }
        });

        // Select default preset (中等画质)
        selectPreset('balanced');
    }

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
        const targetChecked = document.getElementById('targetSizeToggle').checked;
        const mode = targetChecked ? 'target_size' : document.getElementById('compressionMode').value;
        const params = {
            video_encoder: document.getElementById('encoderSelect').value,
            crf: parseInt(document.getElementById('crfSlider').value),
            audio_encoder: 'aac',
            audio_bitrate: document.getElementById('audioBitrate').value,
            output_format: document.getElementById('outputFormat').value,
            resolution: document.getElementById('resolution').value,
            mode: mode,
        };
        if (mode === 'target_size') {
            params.target_percent = _activeTargetPct;
        }
        return params;
    }

    // ============ Compress ============
    window.startCompress = async function() {
        if (!uploadedFiles.length) return;

        const params = getCompressParams();
        const btn = document.getElementById('compressBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> 压缩中...';
        document.getElementById('stopAllBtn').classList.remove('hidden');

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
            document.getElementById('stopAllBtn').classList.add('hidden');
        }
    };

    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(pollTasks, 1000);
        pollTasks();
    }

    async function pollTasks() {
        let notifSent = {};
        try {
            const res = await fetch(API.tasks);
            const data = await res.json();
            renderProgress(data.tasks);
            renderResults(data.tasks);

            const hasRunning = data.tasks.some(t => t.status === 'running' || t.status === 'pending');
            if (hasRunning) {
                document.getElementById('stopAllBtn').classList.remove('hidden');
            }

            const allDone = data.tasks.length > 0 && data.tasks.every(t => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled');
            if (allDone) {
                clearInterval(pollInterval);
                pollInterval = null;
                document.getElementById('compressBtn').disabled = false;
                document.getElementById('compressBtn').innerHTML = '🚀 开始压缩';
                document.getElementById('stopAllBtn').classList.add('hidden');

                // Show export button if there are completed tasks
                const completed = data.tasks.filter(t => t.status === 'completed');
                const exportBtn = document.getElementById('exportReportBtn');
                if (completed.length > 0) {
                    exportBtn.classList.remove('hidden');
                    // Desktop notification
                    if (!notifSent.all) {
                        sendNotification('压缩完成', `${completed.length} 个文件已完成压缩`);
                        notifSent.all = true;
                    }
                }
            }
        } catch(e) { /* ignore */ }
    }

    function renderProgress(tasks) {
        const body = document.getElementById('progressBody');
        const active = tasks.filter(t => t.status !== 'completed' && t.status !== 'failed' && t.status !== 'cancelled');

        if (!active.length && tasks.every(t => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled')) {
            return;
        }

        if (!active.length) {
            body.innerHTML = '<p class="text-muted">暂无任务</p>';
            return;
        }

        body.innerHTML = active.map((t, idx) => {
            const statusText = t.status === 'running' ? '压缩中...' : '等待中';
            const fillClass = t.status === 'failed' ? 'failed' : (t.status === 'cancelled' ? '' : '');

            // 阶段标签
            const phaseMap = { encoding: '编码中', pending: '等待中', cancelled: '已取消', failed: '失败' };
            const phaseLabel = phaseMap[t.phase] || statusText;
            const phaseClassMap = { encoding: 'phase-encoding', analyzing: 'phase-analyzing', completed: 'phase-completed', failed: 'phase-failed', cancelled: 'phase-cancelled' };
            const phaseClass = phaseClassMap[t.phase] || '';

            // ETA 计算
            let etaStr = '--';
            if (t.elapsed > 0 && t.progress > 0 && t.progress < 100) {
                const etaSec = Math.round(t.elapsed / t.progress * (100 - t.progress));
                etaStr = etaSec < 60 ? `${etaSec}秒` : etaSec < 3600 ? `${Math.floor(etaSec/60)}分${etaSec%60}秒` : `${(etaSec/3600).toFixed(1)}小时`;
            }

            // 速度显示
            const speedStr = t.speed && t.speed > 0 ? `${t.speed.toFixed(1)}x` : '--';

            return `
                <div class="task-card" data-task-idx="${idx}">
                    <div class="task-header">
                        <span class="task-name">${t.input_file}</span>
                        <span class="task-status">
                            <span class="phase-tag ${phaseClass}">${phaseLabel}</span>
                        </span>
                    </div>
                    <div class="task-progress-row">
                        <span class="task-progress-pct">${t.progress.toFixed(0)}%</span>
                        <div class="progress-track" style="flex:1">
                            <div class="progress-fill ${fillClass}" style="width:${t.progress}%"></div>
                        </div>
                    </div>
                    <div class="task-extra">
                        <span>⏱ 已用 ${t.elapsed || 0}秒</span>
                        <span>⏳ 预计剩余 ${etaStr}</span>
                        <span>🚀 ${speedStr}</span>
                        <span>📦 ${t.output_size ? formatSize(t.output_size) : '--'}</span>
                    </div>
                    <div class="task-actions">
                        <button class="task-stop-btn" data-task-id="${t.task_id}" onclick="cancelTask('${t.task_id}')"
                            ${t.status !== 'running' ? 'disabled' : ''}>
                            ${t.status === 'running' ? '🛑 停止' : (t.status === 'cancelled' ? '已停止' : '--')}
                        </button>
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

        let html = '';

        // Comparison table (if multiple files)
        if (done.length > 1) {
            html += '<div class="compare-card"><table class="compare-table"><thead><tr>' +
                '<th>文件</th><th>原始</th><th>压缩后</th><th>节省</th><th>耗时</th><th>操作</th>' +
                '</tr></thead><tbody>';
            done.forEach(t => {
                const orig = t.original_size || getOriginalSize(t.input_file) || 0;
                const comp = t.output_size || 0;
                const savings = orig && comp ? ((1 - comp / orig) * 100).toFixed(1) + '%' : '--';
                html += `<tr>
                    <td>${t.output_file}</td>
                    <td>${orig ? formatSize(orig) : '--'}</td>
                    <td>${comp ? formatSize(comp) : '--'}</td>
                    <td class="compare-savings">${savings}</td>
                    <td>${t.elapsed ? t.elapsed.toFixed(0) + '秒' : '--'}</td>
                    <td>
                        <a href="${API.download + encodeURIComponent(t.output_file)}" class="btn btn-primary" style="padding:2px 8px;font-size:12px;text-decoration:none">下载</a>
                        <button class="btn btn-secondary" style="padding:2px 8px;font-size:12px" onclick="previewFile('${t.output_file}')">预览</button>
                    </td>
                </tr>`;
            });
            html += '</tbody></table></div>';
        }

        // Individual result items
        done.forEach((t, i) => {
            const orig = t.original_size || getOriginalSize(t.input_file) || 0;
            const comp = t.output_size || 0;
            const savings = orig && comp ? ((1 - comp / orig) * 100).toFixed(1) : '--';

            html += `
                <div class="result-item">
                    <div>
                        <span class="result-name">${t.output_file}</span>
                        <span class="result-size">${comp ? formatSize(comp) : '--'}</span>
                        <span class="result-savings">(节省 ${savings}%, 原始 ${orig ? formatSize(orig) : '--'})</span>
                    </div>
                    <div style="display:flex;gap:4px">
                        <button class="download-btn" onclick="downloadFile('${t.output_file}')">下载</button>
                        <button class="btn btn-secondary" style="padding:3px 10px;font-size:12px;background:var(--tag-bg)" onclick="previewFile('${t.output_file}')">预览</button>
                    </div>
                </div>
            `;
        });

        // Download all button (include in html)
        if (done.length > 1) {
            html += '<div class="download-all-btn" style="padding:10px 12px"><button class="btn btn-primary" onclick="downloadAll()">📦 下载全部</button></div>';
        }

        body.innerHTML = html;

        // Preview player container (only add once)
        if (!document.getElementById('previewContainer')) {
            const previewDiv = document.createElement('div');
            previewDiv.id = 'previewContainer';
            previewDiv.className = 'hidden';
            body.appendChild(previewDiv);
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
        fetch(API.tasks).then(r => r.json()).then(data => {
            data.tasks.filter(t => t.status === 'completed').forEach(t => {
                window.open(API.download + encodeURIComponent(t.output_file));
            });
        });
    };

    // ============ Cancel Task ============
    window.cancelTask = function(taskId) {
        // Find the button that was clicked
        const taskCard = document.querySelector(`.task-stop-btn[data-task-id="${taskId}"]`);
        if (taskCard) { taskCard.disabled = true; taskCard.textContent = '停止中...'; }
        fetch(API.cancel + taskId, { method: 'POST', keepalive: true })
            .then(r => r.json())
            .then(() => {
                if (taskCard) taskCard.textContent = '已停止';
            })
            .catch(() => { if (taskCard) taskCard.textContent = '失败'; });
    };

    // ============ Stop All Tasks ============
    window.stopAllTasks = function() {
        document.getElementById('stopAllBtn').disabled = true;
        document.getElementById('stopAllBtn').textContent = '停止中...';
        fetch(API.cancelAll, { method: 'POST', keepalive: true })
            .then(r => r.json())
            .then(data => {
                document.getElementById('stopAllBtn').classList.add('hidden');
                document.getElementById('stopAllBtn').disabled = false;
                document.getElementById('stopAllBtn').textContent = '🛑 停止全部';
            })
            .catch(e => {
                document.getElementById('stopAllBtn').disabled = false;
                document.getElementById('stopAllBtn').textContent = '🛑 停止全部';
                alert('停止失败：' + e.message);
            });
    };

    // ============ Dark Mode ============
    window.toggleDarkMode = function() {
        const html = document.documentElement;
        const btn = document.getElementById('darkModeToggle');
        if (html.getAttribute('data-theme') === 'dark') {
            html.removeAttribute('data-theme');
            btn.textContent = '🌙';
        } else {
            html.setAttribute('data-theme', 'dark');
            btn.textContent = '☀️';
        }
        saveSettings();
    };

    // ============ Config Panel ============
    window.toggleConfig = function() {
        const panel = document.getElementById('configPanel');
        panel.classList.toggle('hidden');
    };

    window.updateMaxConcurrent = function() {
        const val = document.getElementById('maxConcurrent').value;
        fetch(API.config, {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({max_concurrent: parseInt(val)}),
        }).catch(() => {});
        saveSettings();
    };

    // ============ Export Report ============
    window.exportReport = function() {
        window.open(API.report);
    };

    // ============ Preview File ============
    window.previewFile = function(filename) {
        const container = document.getElementById('previewContainer');
        const ext = filename.split('.').pop().toLowerCase();
        const videoExts = ['mp4', 'webm', 'mov', 'mkv'];
        const audioExts = ['mp3', 'aac', 'wav', 'flac'];

        if (videoExts.includes(ext)) {
            container.innerHTML = `<video class="preview-player" controls autoplay>
                <source src="${API.preview + encodeURIComponent(filename)}" type="video/${ext === 'mkv' ? 'x-matroska' : ext}">
            </video>`;
        } else if (audioExts.includes(ext)) {
            container.innerHTML = `<audio class="preview-player" controls autoplay style="width:100%">
                <source src="${API.preview + encodeURIComponent(filename)}" type="audio/${ext}">
            </audio>`;
        } else {
            container.innerHTML = `<p class="text-muted">不支持预览此格式</p>`;
        }
        container.classList.remove('hidden');
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
        document.getElementById('stopAllBtn').classList.add('hidden');
        document.getElementById('exportReportBtn').classList.add('hidden');
        document.getElementById('previewContainer').classList.add('hidden');
        fetch(API.clearTemp, { method: 'POST' });
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
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
