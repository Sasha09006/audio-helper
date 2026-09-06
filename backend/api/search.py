import logging
import time
from typing import Annotated

from fastapi import APIRouter, Body, Request

from schemas import ErrorResponse, SearchData, SearchRequest, SuccessResponse
from services.amap_search import search_meeting

logger = logging.getLogger(__name__)

router = APIRouter()

SEARCH_EXAMPLES = {
    "normal": {
        "summary": "正常搜店（杭州东站与龙翔桥）",
        "value": {
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    },
    "ambiguous": {
        "summary": "定位不明确（市民中心）",
        "value": {
            "city_a": "杭州",
            "address_a": "市民中心",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "咖啡店",
        },
    },
    "no_results": {
        "summary": "中点附近无候选",
        "value": {
            "city_a": "杭州",
            "address_a": "杭州东站",
            "city_b": "杭州",
            "address_b": "西湖龙翔桥地铁站",
            "category": "火星补给站",
        },
    },
}


@router.post(
    "/search",
    response_model=SuccessResponse[SearchData],
    responses={
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
    summary="查询中点附近店铺",
)
async def search_nearby_pois(
    request: Request,
    payload: Annotated[SearchRequest, Body(openapi_examples=SEARCH_EXAMPLES)],
) -> SuccessResponse[SearchData]:
    started = time.perf_counter()
    data = await search_meeting(payload)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "stage=search elapsed_ms=%s search_id=%s poi_count=%s",
        elapsed_ms,
        data.search_id,
        len(data.pois),
    )
    return SuccessResponse(request_id=request.state.request_id, data=data)
