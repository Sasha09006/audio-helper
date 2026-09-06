import logging
import time
from typing import Annotated

from fastapi import APIRouter, Body, Request

from schemas import ExtractData, ExtractRequest, ErrorResponse, SuccessResponse
from services.deepseek_extract import extract_meeting

logger = logging.getLogger(__name__)

router = APIRouter()

EXTRACT_EXAMPLES = {
    "normal": {
        "summary": "正常提取（同城两人）",
        "value": {
            "text": "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店",
            "city": "杭州",
        },
    },
    "page_city": {
        "summary": "未说城市，使用页面选定城市；喝咖啡归一化",
        "value": {
            "text": "我在东站，朋友在龙翔桥地铁站，帮我们找个地方喝咖啡",
            "city": "杭州",
        },
    },
    "spoken_city": {
        "summary": "口述城市优先于页面默认城市",
        "value": {
            "text": "我在上海人民广场，朋友在静安寺，找个餐厅",
            "city": "杭州",
        },
    },
    "missing_address": {
        "summary": "地址缺失",
        "value": {
            "text": "我和朋友想喝咖啡",
            "city": "杭州",
        },
    },
    "vague_address": {
        "summary": "含糊地址（我家 / 公司）",
        "value": {
            "text": "我在我家，朋友在公司，找个咖啡店",
            "city": "杭州",
        },
    },
    "party_count": {
        "summary": "人数不符",
        "value": {
            "text": "我、小明和小红都在西湖边，想找个餐厅",
            "city": "杭州",
        },
    },
    "cross_city": {
        "summary": "跨城",
        "value": {
            "text": "我在杭州东站，朋友在上海虹桥站，找个咖啡店",
            "city": "杭州",
        },
    },
}


@router.post(
    "/extract",
    response_model=SuccessResponse[ExtractData],
    responses={
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
    summary="提取碰面信息",
)
async def extract_meeting_info(
    request: Request,
    payload: Annotated[ExtractRequest, Body(openapi_examples=EXTRACT_EXAMPLES)],
) -> SuccessResponse[ExtractData]:
    started = time.perf_counter()
    data = await extract_meeting(payload.text, payload.city)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "stage=extract elapsed_ms=%s text_len=%s category=%s",
        elapsed_ms,
        len(payload.text),
        data.category,
    )
    return SuccessResponse(request_id=request.state.request_id, data=data)
