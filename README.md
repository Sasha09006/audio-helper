# 语音约碰面地点

按住网页录音按钮，说出两人所在地点和想找的店，系统完成识别、提取、搜店和语音播报。

当前进度：后端 `POST /upload`、`POST /asr`、`POST /extract` 与前端本地录音已实现，前端尚未调用业务接口。

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

## 验证 POST /asr

先重启后端（新路由不会热更新）：

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8003
```

本轮不要从页面调用 `/asr`。

### Mock 测试（无密钥、无调用费用）

我没有执行这些命令。由你在 `backend` 目录运行：

```bash
cd backend
source .venv/bin/activate
pytest tests/test_asr.py -v
```

覆盖：缺字段 422、编号不存在或过期 404、Mock 识别成功 200、空识别 422、供应商超时 504、供应商异常 502、Base64 超限 413。Mock 通过不能证明真实百炼已跑通。

### 真实识别（需要 BAILIAN_API_KEY，会产生调用费用）

1. 在 `backend/.env` 填写北京地域的 `BAILIAN_API_KEY`，保存后重启 uvicorn。
2. 打开 http://localhost:8003/docs 。
3. 先调用 `POST /upload`，上传刚下载的真实 `recording-*.webm`。
4. 复制响应里的 `data.audio_id`，例如 `aud_20260906_155845_4d631d`。
5. 打开 `POST /asr` → Try it out，请求体填入：

```json
{
  "audio_id": "aud_20260906_155845_4d631d"
}
```

把编号换成上一步真实返回值。Execute。

**正常（200）** — `data.text` 应是这段录音的识别结果，不是固定示例句：

```json
{
  "request_id": "req_20260906_161900_abc123",
  "data": {
    "text": "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店"
  }
}
```

实际文字随录音内容变化。

**编号不存在或已过期（404，无费用）**：

```json
{
  "audio_id": "aud_20990101_000000_ffffff"
}
```

```json
{
  "request_id": "req_20260906_161901_abc123",
  "error": {
    "code": "AUDIO_NOT_FOUND",
    "message": "音频文件不存在或已过期",
    "stage": "asr"
  }
}
```

**缺字段（422，无费用）**：请求体 `{}`。

```json
{
  "request_id": "req_20260906_161902_abc123",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求缺字段或字段类型错误",
    "stage": "asr"
  }
}
```

**空识别（422，会调用百炼）**：上传几乎无语音的合法 1—60 秒 WebM/Opus 后再识别。

```json
{
  "request_id": "req_20260906_161903_abc123",
  "error": {
    "code": "EMPTY_RECOGNITION",
    "message": "未识别到有效语音内容，请重新录音",
    "stage": "asr"
  }
}
```

**密钥无效或服务异常（502，可能产生一次失败调用）**：填错误密钥或断网后再识别。

```json
{
  "request_id": "req_20260906_161904_abc123",
  "error": {
    "code": "ASR_SERVICE_ERROR",
    "message": "语音识别服务暂时不可用，请稍后重试",
    "stage": "asr"
  }
}
```

未填密钥时接口不会发往百炼，同样返回上述 502。超时返回 504，`code` 为 `ASR_TIMEOUT`。

接口总预算 20 秒，其中百炼调用 15 秒，编码与校验约 5 秒。Base64 编码后上限 10MB（官方限制）；原文件仍是 5MB。

## 验证 POST /extract

先重启后端（新路由不会热更新）。**不要**再执行 `cp .env.example .env`，以免清空已填密钥：

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8003
```

本轮不要从页面调用 `/extract`。提示词在 `backend/prompts/extract.txt`，含 json 格式示例。

模型内部 JSON 有 7 个字段：`city_a`、`address_a`、`city_b`、`address_b`、`category`、`party_count`、`incomplete_reason`，缺失值允许为 `null`。后端先校验这 7 个字段的结构，再判断业务完整性。只有完整时，接口 `data` 才返回约定的 5 个业务字段，不含诊断字段。

### Mock 测试（无密钥、无调用费用）

我没有执行这些命令。由你在 `backend` 目录运行：

```bash
cd backend
source .venv/bin/activate
pytest tests/test_extract.py -v
```

覆盖：缺字段 422、Mock 成功 200 且不含诊断字段、人数不符 / 地址缺失 / 含糊地址 / 跨城 422、口述城市优先、页面城市回填、类别归一化、缺字段或非法 JSON 为 `EXTRACT_FORMAT_ERROR`（502，不是信息不足）、超时 504、供应商异常与未填密钥 502。Mock 通过不能证明真实 DeepSeek 已跑通。

### 真实提取（需要 DEEPSEEK_API_KEY，会产生调用费用）

1. 在 `backend/.env` 填写 `DEEPSEEK_API_KEY`，保存后重启 uvicorn（不要覆盖 `.env`）。
2. 打开 http://localhost:8003/docs ，找到 `POST /extract` → Try it out。Examples 下拉可直接选下列场景，或把请求体粘进去后 Execute。

下面每组都写了：**可粘贴的请求**、**模型内部原始 JSON（7 字段，不会出现在成功响应里）**、**接口最终返回**。真实模型用词可能略有出入，但业务结论应一致。

**1. 正常提取（200）**

```json
{
  "text": "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店",
  "city": "杭州"
}
```

模型原始输出（内部）：

```json
{
  "city_a": "杭州",
  "address_a": "杭州东站",
  "city_b": "杭州",
  "address_b": "西湖龙翔桥地铁站",
  "category": "咖啡店",
  "party_count": 2,
  "incomplete_reason": null
}
```

接口最终返回：

```json
{
  "request_id": "req_20260906_170100_abc123",
  "data": {
    "city_a": "杭州",
    "address_a": "杭州东站",
    "city_b": "杭州",
    "address_b": "西湖龙翔桥地铁站",
    "category": "咖啡店"
  }
}
```

`data` 里不应出现 `party_count` 或 `incomplete_reason`。

**2. 未说城市，使用页面选定城市；「喝咖啡」归一化为咖啡店（200）**

```json
{
  "text": "我在东站，朋友在龙翔桥地铁站，帮我们找个地方喝咖啡",
  "city": "杭州"
}
```

模型原始输出（内部，城市由口述缺失而填页面城市）：

```json
{
  "city_a": "杭州",
  "address_a": "东站",
  "city_b": "杭州",
  "address_b": "龙翔桥地铁站",
  "category": "咖啡店",
  "party_count": 2,
  "incomplete_reason": null
}
```

接口最终返回：`data` 五个业务字段，`category` 为 `咖啡店`。

**3. 口述城市优先于页面默认杭州（200）**

```json
{
  "text": "我在上海人民广场，朋友在静安寺，找个餐厅",
  "city": "杭州"
}
```

模型原始输出（内部）城市应为上海，不能被页面杭州覆盖。接口 `data.city_a` / `data.city_b` 为 `上海`，`category` 为 `餐厅`。

**4. 地址缺失（422）**

```json
{
  "text": "我和朋友想喝咖啡",
  "city": "杭州"
}
```

模型原始输出（内部）地址为 `null`，`party_count` 为 2，`incomplete_reason` 说明缺地址。接口最终返回：

```json
{
  "request_id": "req_20260906_170101_abc123",
  "error": {
    "code": "INCOMPLETE_ADDRESS",
    "message": "未能识别出双方的具体地点，请补充完整地址后重新录音",
    "stage": "extract"
  }
}
```

**5. 含糊地址「我家 / 公司」（422）**

```json
{
  "text": "我在我家，朋友在公司，找个咖啡店",
  "city": "杭州"
}
```

模型原始输出（内部）不应把「我家」「公司」写成可定位地址，应为 `null`。接口最终仍是 `INCOMPLETE_ADDRESS`，与上一组同一错误结构。

**6. 人数不符（422）**

```json
{
  "text": "我、小明和小红都在西湖边，想找个餐厅",
  "city": "杭州"
}
```

模型原始输出（内部）`party_count` 为 3。接口最终返回：

```json
{
  "request_id": "req_20260906_170102_abc123",
  "error": {
    "code": "INVALID_PARTY_COUNT",
    "message": "当前版本仅支持两人约碰面，请重新表达",
    "stage": "extract"
  }
}
```

**7. 跨城（422）**

```json
{
  "text": "我在杭州东站，朋友在上海虹桥站，找个咖啡店",
  "city": "杭州"
}
```

模型原始输出（内部）双方城市不同，`incomplete_reason` 可为「跨城」。接口最终返回：

```json
{
  "request_id": "req_20260906_170103_abc123",
  "error": {
    "code": "CROSS_CITY",
    "message": "当前版本仅支持同城碰面，请重新表达",
    "stage": "extract"
  }
}
```

**缺字段（422，无费用）**：请求体 `{}` 或只有 `text`。

```json
{
  "request_id": "req_20260906_170104_abc123",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求缺字段或字段类型错误",
    "stage": "extract"
  }
}
```

**模型格式异常（502，会调用 DeepSeek）**：无法用正常口述稳定复现。若供应商返回非法 JSON、缺约定字段或类型错误，接口 `code` 为 `EXTRACT_FORMAT_ERROR`，**不是** `INCOMPLETE_ADDRESS`。

```json
{
  "request_id": "req_20260906_170105_abc123",
  "error": {
    "code": "EXTRACT_FORMAT_ERROR",
    "message": "信息提取服务返回异常，请稍后重试",
    "stage": "extract"
  }
}
```

**密钥无效或服务异常（502）**：未填或填错 `DEEPSEEK_API_KEY`。未填时不会发往 DeepSeek。

```json
{
  "request_id": "req_20260906_170106_abc123",
  "error": {
    "code": "EXTRACT_SERVICE_ERROR",
    "message": "信息提取服务暂时不可用，请稍后重试",
    "stage": "extract"
  }
}
```

超时返回 504，`code` 为 `EXTRACT_TIMEOUT`。

接口总预算 15 秒，其中 DeepSeek 调用 12 秒，解析与校验约 3 秒。调用使用官方 Chat Completions：`response_format.type=json_object`，`thinking.type=disabled`，模型 `deepseek-v4-flash`。

## 测试说明

自动化 Mock 测试不能证明真实 ASR、DeepSeek、高德、TTS 已跑通。真实调用需填写密钥并由你确认后再执行。
