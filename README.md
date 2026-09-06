# 语音约碰面地点

按住网页录音按钮，说出两人所在地点和想找的店，系统完成识别、提取、搜店和语音播报。

当前进度：项目骨架。已提供后端 `GET /health` 和可打开的前端基础页面。录音与其他业务接口尚未实现。

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

## 测试说明

自动化测试与真实 ASR、DeepSeek、高德、TTS 验收尚未接入。Mock 通过不能证明真实链路已跑通；真实调用需填写密钥并由你确认后再执行。
