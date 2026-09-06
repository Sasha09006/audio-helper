import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from config import settings

app = FastAPI(
    title="语音约碰面地点",
    version="0.1.0",
    description="第一版骨架：仅提供健康检查。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)


def generate_request_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"req_{stamp}_{suffix}"


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request.state.request_id = generate_request_id()
    return await call_next(request)
