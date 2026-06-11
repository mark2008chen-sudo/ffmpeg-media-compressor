# SKILL.md

## 名称
ffmpeg-media-compressor

## 描述
使用FFmpeg压缩视频/音频文件，保持画质不变，支持CPU/GPU编码。自动安装FFmpeg、批量处理、预测输出大小，适合小白用户。

## 何时使用
- 用户说"压缩视频"、"减小文件体积"、"批量压缩音频"
- 用户上传视频/音频文件，要求减小大小
- 用户需要选择CPU或GPU进行编码加速

## 使用方法

### 1. 启动服务
```bash
cd D:/Software/ffmpeg-media-compressor
pip install -r requirements.txt
python -m backend.main
```

### 2. 打开浏览器
访问 http://localhost:8080

### 3. 使用流程
1. 等待FFmpeg自动检测（如未安装，点击"一键安装"）
2. 拖拽或选择视频/音频文件
3. 选择压缩模式（平衡模式/保持画质/强力压缩）
4. **选择编码方式：CPU (libx264/x265) 或 GPU (自动检测NVENC/AMF/QSV)**
5. 可选：点击"预测大小"查看压缩后文件大小
6. 点击"开始压缩"
7. 等待完成，下载文件

## 核心功能
| 功能 | 说明 |
|------|------|
| CPU/GPU编码 | 自动检测NVIDIA NVENC、AMD AMF、Intel QSV等硬件编码器 |
| 输出大小预测 | 采样分析+码率估算，准确率>85% |
| 批量处理 | 同时处理多个文件，支持队列管理 |
| 自动安装FFmpeg | 检测不到FFmpeg时自动下载安装 |
| 格式转换 | 支持MP4/MKV/MOV/WebM/MP3/AAC/FLAC/WAV |
| 预设压缩模式 | 平衡模式/保持画质/强力压缩 |

## 文件结构
```
D:/Software/ffmpeg-media-compressor/
├── SKILL.md
├── requirements.txt
├── start.bat / start.sh
├── backend/
│   ├── main.py              # Flask服务器
│   ├── ffmpeg_manager.py    # FFmpeg管理
│   ├── compression_engine.py # 压缩引擎
│   ├── size_predictor.py    # 大小预测
│   └── templates/index.html
├── static/
│   ├── css/style.css
│   └── js/app.js
├── temp/                    # 临时文件
├── bin/                     # FFmpeg二进制
└── references/              # 参考文档
```
