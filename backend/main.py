import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.asr import router as asr_router
from api.extract import router as extract_router
from api.health import router as health_router
from api.search import router as search_router
from api.upload import router as upload_router
from config import settings
from errors import AppError
from services.audio_store import purge_expired_audio
from services.search_store import purge_expired_searches

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    purge_expired_audio()
    purge_expired_searches()
    yield


app = FastAPI(
    title="语音约碰面地点",
    version="0.1.0",
    description="当前提供健康检查、录音上传、语音识别、信息提取与中点搜店。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(upload_router)
app.include_router(asr_router)
app.include_router(extract_router)
app.include_router(search_router)


def generate_request_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"req_{stamp}_{suffix}"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or generate_request_id()


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request.state.request_id = generate_request_id()
    return await call_next(request)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning("stage=%s code=%s", exc.stage, exc.code)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "request_id": _request_id(request),
            "error": {
                "code": exc.code,
                "message": exc.message,
                "stage": exc.stage,
            },
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    stage = _stage_from_path(request.url.path)
    missing_file = any(_is_file_field(error) for error in exc.errors())
    code = "MISSING_FILE" if missing_file and stage == "upload" else "VALIDATION_ERROR"
    message = "未检测到音频文件" if code == "MISSING_FILE" else "请求缺字段或字段类型错误"
    logger.warning("stage=%s code=%s", stage, code)
    return JSONResponse(
        status_code=422,
        content={
            "request_id": _request_id(request),
            "error": {
                "code": code,
                "message": message,
                "stage": stage,
            },
        },
    )


def _stage_from_path(path: str) -> str:
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name in {"upload", "asr", "extract", "search", "finalize", "health"}:
        return name
    return "request"


def _is_file_field(error: dict) -> bool:
    loc = error.get("loc") or ()
    return "file" in loc
