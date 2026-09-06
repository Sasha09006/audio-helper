from fastapi import APIRouter, Request

from schemas import HealthData, SuccessResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=SuccessResponse[HealthData],
    summary="健康检查",
)
def health_check(request: Request) -> SuccessResponse[HealthData]:
    return SuccessResponse(
        request_id=request.state.request_id,
        data=HealthData(status="ok"),
    )
