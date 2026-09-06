# 语音约碰面地点

按住网页录音按钮，说出两人所在地点和想找的店，系统完成识别、提取、搜店和语音播报。

当前进度：后端 `POST /upload` 与前端本地录音已实现，二者尚未对接。已提供 `GET /health`、城市选择、按住录音、本地试听和临时下载。

本地环境：Python 3.11，Node.js 22.12 及以上的 22.x。后端端口 8003，前端端口 5175。

## 启动后端

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 127.0.0.1 --port 8003
```

未填写 `BAILIAN_API_KEY`、`DEEPSEEK_API_KEY`、`AMAP_API_KEY` 时，健康检查仍应可用。

## 启动前端

另开终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5175 或 http://127.0.0.1:5175 。

## 验证健康检查

浏览器或命令行访问：

- http://localhost:8003/health
- http://localhost:8003/docs 中调用 `GET /health`

预期：HTTP 200，JSON 形如：

```json
{
  "request_id": "req_20260906_143025_abc123",
  "data": {
    "status": "ok"
  }
}
```

`request_id` 每次请求会变化。本轮不调用外部服务。

## 验证前端录音

前端已启动时刷新 http://127.0.0.1:5175 。本轮不需要后端。

1. 页面默认城市为杭州，可改为其他选项。
2. 按住红色按钮说话，松开后应出现试听控件和「下载录音文件」。
3. 鼠标按住后移出按钮再松开，也应结束录音并释放麦克风，而不是一直占用。
4. 录音中按 `Esc` 或点「取消」，应丢弃本次录音并释放麦克风。
5. 按住约 60 秒应自动结束，并可以试听。
6. 按下后立刻松开（不足 1 秒）应提示时长不合规。
7. 拒绝麦克风权限后应提示授权，且不出现下载入口。
8. 不支持 WebM/Opus 的浏览器应提示更换 Chrome 或 Edge。

页面不应出现虚构的识别文字或店铺结果，也不应请求 `/upload`。

## 检查下载文件的格式、大小和时长

下载后，在终端检查（把路径换成实际文件）：

```bash
file ~/Downloads/recording-*.webm
ls -lh ~/Downloads/recording-*.webm
```

预期：`file` 能看到 WebM / Opus；大小小于 5MB；页面显示的时长与实际说话时间接近，且在 1—60 秒。

若已安装 ffmpeg：

```bash
ffprobe -v error -show_entries format=format_name:format=duration:stream=codec_name \
  -of default=nw=1 ~/Downloads/recording-xxxx.webm
```

预期：`format_name` 含 `webm`，`codec_name` 为 `opus`，`duration` 约等于页面显示秒数。部分浏览器录音可能缺少 Duration 元数据，此时以页面按按住时间计算的秒数为准。

也可用 Python 与 mutagen 查看（mutagen 不能解析 WebM，浏览器录音通常会打印 `None`；后端用 EBML/Opus 探测，不依赖 mutagen 或 ffprobe）：

```bash
cd backend
source .venv/bin/activate
python - <<'PY'
from pathlib import Path
from mutagen import File
path = Path.home() / "Downloads" / "请改成实际文件名.webm"
info = File(path)
print("size_bytes", path.stat().st_size)
print("mutagen", info)
PY
```

## 验证 POST /upload

后端启动后打开 http://localhost:8003/docs ，找到 `POST /upload`。

1. 点 Try it out。
2. 在 `file` 选择刚从页面下载的 `recording-*.webm`。字段名必须是 `file`。
3. Execute。

本轮不要从页面点上传；前端尚未调用该接口。

### 正常上传（200）

用 1—60 秒、小于 5MB 的真实 WebM/Opus 录音。

```json
{
  "request_id": "req_20260906_153700_abc123",
  "data": {
    "audio_id": "aud_20260906_153700_def456"
  }
}
```

`audio_id` 是临时编号，不是文件路径。对应文件在 `backend/storage/uploads/{audio_id}.webm`，元数据在 `backend/storage/uploads/{audio_id}.json`（含 `created_at`，供后续 24 小时有效期校验）。响应里不会出现这两条路径。

### 格式不支持（415）

上传任意文本或图片，例如把 `.txt` 改名为 `.webm`，或直接上传 `.jpg`。

```json
{
  "request_id": "req_20260906_153701_abc123",
  "error": {
    "code": "UNSUPPORTED_FORMAT",
    "message": "仅支持WebM/Opus格式音频",
    "stage": "upload"
  }
}
```

### 文件过大（413）

先做一个超过 5MB 的文件（内容不必是合法音频，体积先被拦截）：

```bash
dd if=/dev/zero of=/tmp/too-large.webm bs=1048576 count=6
```

在 /docs 上传该文件：

```json
{
  "request_id": "req_20260906_153702_abc123",
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "音频文件超过5MB限制",
    "stage": "upload"
  }
}
```

### 时长不合规（422）

需要合法 WebM/Opus，但时长 <1 秒或 >60 秒。若本机有 ffmpeg（可选，未安装也不影响服务运行）：

```bash
ffmpeg -y -i ~/Downloads/recording-xxxx.webm -t 0.3 -c copy /tmp/too-short.webm
ffmpeg -y -stream_loop -1 -i ~/Downloads/recording-xxxx.webm -t 61 -c copy /tmp/too-long.webm
```

上传 `/tmp/too-short.webm` 或 `/tmp/too-long.webm`：

```json
{
  "request_id": "req_20260906_153703_abc123",
  "error": {
    "code": "INVALID_DURATION",
    "message": "录音时长必须在1-60秒之间",
    "stage": "upload"
  }
}
```

未带 `file` 字段时也是 422，`code` 为 `MISSING_FILE`。

缺少 Duration 元数据的浏览器录音只要容器、Opus 编码和由时间戳算出的时长合法，应返回 200，不能仅因没有 Duration 被判非法。

运行时不需要 ffprobe。若要人工对照时长，可自行安装 ffmpeg（macOS: `brew install ffmpeg`），再用上一节的 `ffprobe` 命令查看下载文件。安装探测工具不等于转码，服务也不会调用它。

## 测试说明

自动化测试与真实 ASR、DeepSeek、高德、TTS 验收尚未接入。Mock 通过不能证明真实链路已跑通；真实调用需填写密钥并由你确认后再执行。
