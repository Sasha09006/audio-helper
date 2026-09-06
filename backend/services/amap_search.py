import asyncio
import logging
import math
import re
from dataclasses import dataclass

import httpx

from config import settings
from errors import AppError
from schemas import Midpoint, PoiItem, SearchData, SearchRequest
from services.search_store import save_search

logger = logging.getLogger(__name__)


class AmapEngineError(Exception):
    """高德 infocode 30001：引擎无数据或短暂异常，由调用方决定业务含义。"""

EARTH_RADIUS_M = 6_371_000
CITY_SUFFIXES = (
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "自治区",
    "特别行政区",
    "省",
    "市",
)
COARSE_LEVELS = {"国家", "省", "市", "区县", "开发区"}
ADMIN_RE = re.compile(r"(特别行政区|自治区|省|市|区|县|镇|乡|街道)")
PLACE_PUNCT_RE = re.compile(r"[\(\)（）\[\]【】\-—_/]")
STATION_SUFFIXES = ("地铁站", "公交站", "火车站", "高铁站", "站")
AMAP_ENGINE_ERROR = "30001"


@dataclass(frozen=True)
class GeoPoint:
    longitude: float
    latitude: float
    formatted_address: str
    level: str


def amap_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def normalize_city(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    for suffix in CITY_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text or None


def parse_amap_location(raw: object) -> tuple[float, float] | None:
    text = amap_text(raw)
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        longitude = float(parts[0])
        latitude = float(parts[1])
    except ValueError:
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    if longitude == 0.0 and latitude == 0.0:
        return None
    return longitude, latitude


def haversine_m(
    longitude_a: float,
    latitude_a: float,
    longitude_b: float,
    latitude_b: float,
) -> float:
    phi1 = math.radians(latitude_a)
    phi2 = math.radians(latitude_b)
    d_phi = math.radians(latitude_b - latitude_a)
    d_lambda = math.radians(longitude_b - longitude_a)
    chord = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(chord))


def geographic_midpoint(point_a: GeoPoint, point_b: GeoPoint) -> Midpoint:
    return Midpoint(
        longitude=(point_a.longitude + point_b.longitude) / 2,
        latitude=(point_a.latitude + point_b.latitude) / 2,
    )


def _compact(value: str) -> str:
    return "".join(value.split())


def fold_admin(value: str) -> str:
    return PLACE_PUNCT_RE.sub("", ADMIN_RE.sub("", _compact(value)))


def city_matches(geocode: dict, city: str) -> bool:
    expected = normalize_city(city)
    if expected is None:
        return False
    haystacks = [
        amap_text(geocode.get("city")),
        amap_text(geocode.get("province")),
        amap_text(geocode.get("formatted_address")),
    ]
    return any(expected in (normalize_city(item) or "") or expected in _compact(item) for item in haystacks if item)


def is_precise_level(level: str) -> bool:
    if not level:
        return False
    return level not in COARSE_LEVELS


def name_matches(address: str, geocode: dict, city: str) -> bool:
    query = fold_admin(address)
    city_core = fold_admin(city)
    if city_core and query.startswith(city_core):
        query = query[len(city_core) :]
    if not query:
        query = fold_admin(address)
    hay = fold_admin(
        "".join(
            [
                amap_text(geocode.get("formatted_address")),
                amap_text(geocode.get("district")),
                amap_text(geocode.get("street")),
                amap_text(geocode.get("number")),
            ]
        )
    )
    if not query or not hay:
        return False
    if query in hay or hay in query:
        return True
    core = query
    for suffix in STATION_SUFFIXES:
        if core.endswith(suffix) and len(core) > len(suffix):
            core = core[: -len(suffix)]
            break
    for size in range(len(core), 2, -1):
        piece = core[-size:]
        if piece in hay:
            return size >= 3
    return False


def _similar_address(left: str, right: str) -> bool:
    a = _compact(left)
    b = _compact(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _is_duplicate(left: GeoPoint, right: GeoPoint) -> bool:
    distance = haversine_m(left.longitude, left.latitude, right.longitude, right.latitude)
    return distance <= settings.geocode_duplicate_max_m and _similar_address(
        left.formatted_address,
        right.formatted_address,
    )


def pick_geocode(geocodes: object, city: str, address: str) -> GeoPoint:
    if not isinstance(geocodes, list):
        raise AppError(
            422,
            "GEOCODE_FAILED",
            f'无法定位"{address}"，请确认地址后重新录音',
            "search",
        )

    candidates: list[GeoPoint] = []
    for item in geocodes:
        if not isinstance(item, dict):
            continue
        coords = parse_amap_location(item.get("location"))
        level = amap_text(item.get("level"))
        formatted = amap_text(item.get("formatted_address"))
        if coords is None or not formatted:
            continue
        if not city_matches(item, city) or not is_precise_level(level):
            continue
        if not name_matches(address, item, city):
            continue
        candidates.append(
            GeoPoint(
                longitude=coords[0],
                latitude=coords[1],
                formatted_address=formatted,
                level=level,
            )
        )

    if not candidates:
        raise AppError(
            422,
            "GEOCODE_FAILED",
            f'无法定位"{address}"，请确认地址后重新录音',
            "search",
        )

    clusters: list[list[GeoPoint]] = []
    for candidate in candidates:
        placed = False
        for cluster in clusters:
            if _is_duplicate(candidate, cluster[0]):
                cluster.append(candidate)
                placed = True
                break
        if not placed:
            clusters.append([candidate])

    if len(clusters) > 1:
        logger.warning(
            "stage=search code=AMBIGUOUS_LOCATION address=%s clusters=%s",
            address,
            len(clusters),
        )
        raise AppError(
            422,
            "AMBIGUOUS_LOCATION",
            f'"{address}"有多个匹配结果，请补充具体地点后重新录音',
            "search",
        )
    return clusters[0][0]


def parse_distance_m(raw: object) -> float | None:
    text = amap_text(raw)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0 or not math.isfinite(value):
        return None
    return value


def collect_valid_pois(pois: object, midpoint: Midpoint) -> list[PoiItem]:
    if not isinstance(pois, list):
        return []
    valid: list[PoiItem] = []
    for item in pois:
        if not isinstance(item, dict):
            continue
        name = amap_text(item.get("name"))
        address = amap_text(item.get("address"))
        coords = parse_amap_location(item.get("location"))
        if not name or not address or coords is None:
            continue
        reported = parse_distance_m(item.get("distance"))
        if reported is None:
            distance = haversine_m(
                midpoint.longitude,
                midpoint.latitude,
                coords[0],
                coords[1],
            )
        else:
            distance = reported
        valid.append(
            PoiItem(
                name=name,
                address=address,
                distance_to_midpoint_m=int(round(distance)),
            )
        )
    valid.sort(key=lambda poi: poi.distance_to_midpoint_m)
    return valid[: settings.max_poi_results]


def _require_amap_ok(body: object, *, vendor_status: int) -> dict:
    if vendor_status >= 400:
        logger.warning("stage=search code=AMAP_SERVICE_ERROR vendor_status=%s", vendor_status)
        raise AppError(502, "AMAP_SERVICE_ERROR", "地图服务暂时不可用，请稍后重试", "search")
    if not isinstance(body, dict):
        logger.warning("stage=search code=AMAP_SERVICE_ERROR reason=invalid_json")
        raise AppError(502, "AMAP_SERVICE_ERROR", "地图服务暂时不可用，请稍后重试", "search")
    if amap_text(body.get("status")) != "1":
        infocode = amap_text(body.get("infocode"))
        info = amap_text(body.get("info"))
        logger.warning(
            "stage=search amap_status=0 infocode=%s info=%s",
            infocode,
            info,
        )
        if infocode == AMAP_ENGINE_ERROR:
            raise AmapEngineError(info or infocode)
        raise AppError(502, "AMAP_SERVICE_ERROR", "地图服务暂时不可用，请稍后重试", "search")
    return body


async def _amap_get(client: httpx.AsyncClient, url: str, params: dict, timeout: int) -> dict:
    try:
        response = await client.get(url, params=params, timeout=timeout)
    except httpx.TimeoutException:
        logger.warning("stage=search code=SEARCH_TIMEOUT")
        raise AppError(504, "SEARCH_TIMEOUT", "搜索超时，请稍后重试", "search") from None
    except httpx.RequestError:
        logger.warning("stage=search code=AMAP_SERVICE_ERROR reason=network")
        raise AppError(502, "AMAP_SERVICE_ERROR", "地图服务暂时不可用，请稍后重试", "search") from None
    try:
        body = response.json()
    except ValueError:
        logger.warning("stage=search code=AMAP_SERVICE_ERROR reason=invalid_json")
        raise AppError(502, "AMAP_SERVICE_ERROR", "地图服务暂时不可用，请稍后重试", "search") from None
    return _require_amap_ok(body, vendor_status=response.status_code)


async def geocode_address(client: httpx.AsyncClient, city: str, address: str) -> GeoPoint:
    params = {
        "key": settings.amap_api_key,
        "address": address,
        "city": city,
    }
    try:
        body = await _amap_get(
            client,
            settings.amap_geocode_endpoint,
            params,
            settings.timeout_search_geocode,
        )
    except AmapEngineError:
        logger.warning("stage=search code=GEOCODE_FAILED reason=engine_error")
        raise AppError(
            422,
            "GEOCODE_FAILED",
            f'无法定位"{address}"，请确认地址后重新录音',
            "search",
        ) from None
    return pick_geocode(body.get("geocodes"), city, address)


async def search_around(
    client: httpx.AsyncClient,
    midpoint: Midpoint,
    category: str,
    radius_m: int,
) -> list[PoiItem]:
    params = {
        "key": settings.amap_api_key,
        "location": f"{midpoint.longitude},{midpoint.latitude}",
        "keywords": category,
        "radius": radius_m,
        "offset": 25,
        "sortrule": "distance",
        "extensions": "base",
    }
    try:
        body = await _amap_get(
            client,
            settings.amap_poi_search_endpoint,
            params,
            settings.timeout_search_poi,
        )
    except AmapEngineError:
        logger.warning("stage=search around engine_error radius_m=%s", radius_m)
        return []
    return collect_valid_pois(body.get("pois"), midpoint)


async def _search_meeting(payload: SearchRequest) -> SearchData:
    if not settings.amap_api_key:
        logger.warning("stage=search code=AMAP_SERVICE_ERROR reason=missing_vendor_config")
        raise AppError(502, "AMAP_SERVICE_ERROR", "地图服务暂时不可用，请稍后重试", "search")

    city_a = normalize_city(payload.city_a)
    city_b = normalize_city(payload.city_b)
    if city_a is None or city_b is None or city_a != city_b:
        raise AppError(422, "CROSS_CITY", "当前版本仅支持同城碰面，请重新表达", "search")

    async with httpx.AsyncClient() as client:
        point_a, point_b = await asyncio.gather(
            geocode_address(client, payload.city_a, payload.address_a),
            geocode_address(client, payload.city_b, payload.address_b),
        )
        midpoint = geographic_midpoint(point_a, point_b)
        pois = await search_around(
            client,
            midpoint,
            payload.category,
            settings.default_search_radius_m,
        )
        radius_used = settings.default_search_radius_m
        if not pois:
            pois = await search_around(
                client,
                midpoint,
                payload.category,
                settings.extended_search_radius_m,
            )
            radius_used = settings.extended_search_radius_m

    if not pois:
        raise AppError(
            422,
            "NO_RESULTS",
            f"中点附近5公里内未找到{payload.category}，请调整需求后重试",
            "search",
        )

    stored = save_search(
        {
            "city_a": payload.city_a,
            "address_a": payload.address_a,
            "city_b": payload.city_b,
            "address_b": payload.address_b,
            "category": payload.category,
            "location_a": {
                "longitude": point_a.longitude,
                "latitude": point_a.latitude,
                "formatted_address": point_a.formatted_address,
                "level": point_a.level,
            },
            "location_b": {
                "longitude": point_b.longitude,
                "latitude": point_b.latitude,
                "formatted_address": point_b.formatted_address,
                "level": point_b.level,
            },
            "midpoint": {
                "longitude": midpoint.longitude,
                "latitude": midpoint.latitude,
            },
            "radius_m": radius_used,
            "pois": [poi.model_dump() for poi in pois],
        }
    )
    logger.info(
        "stage=search search_id=%s poi_count=%s radius_m=%s",
        stored.search_id,
        len(pois),
        radius_used,
    )
    return SearchData(search_id=stored.search_id, midpoint=midpoint, pois=pois)


async def search_meeting(payload: SearchRequest) -> SearchData:
    try:
        return await asyncio.wait_for(
            _search_meeting(payload),
            timeout=settings.timeout_search,
        )
    except TimeoutError:
        logger.warning("stage=search code=SEARCH_TIMEOUT reason=budget")
        raise AppError(504, "SEARCH_TIMEOUT", "搜索超时，请稍后重试", "search") from None
