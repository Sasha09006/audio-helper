import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from errors import AppError
from main import app
from schemas import Midpoint, SearchData, SearchRequest
from services import amap_search


client = TestClient(app)

POINT_EAST = (120.2125, 30.2909)
POINT_LONGXIANG = (120.1610, 30.2590)


def geo_item(lng, lat, formatted, level="兴趣点", city="杭州市", **extra):
    item = {
        "formatted_address": formatted,
        "city": city,
        "district": extra.get("district", "上城区"),
        "street": extra.get("street", ""),
        "number": extra.get("number", ""),
        "location": f"{lng},{lat}",
        "level": level,
    }
    return item


def geocode_body(*items):
    return {"status": "1", "infocode": "10000", "geocodes": list(items)}


def poi_item(name, address, lng, lat, distance=None):
    payload = {
        "name": name,
        "address": address,
        "location": f"{lng},{lat}",
    }
    if distance is not None:
        payload["distance"] = distance
    return payload


def around_body(*pois):
    return {"status": "1", "infocode": "10000", "pois": list(pois)}


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeClient:
    def __init__(self, geocodes=None, around=None, error=None, capture=None):
        self.geocodes = geocodes or {}
        self.around = around or {}
        self.error = error
        self.capture = capture if capture is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, timeout=None):
        self.capture.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if self.error:
            raise self.error
        if "geocode" in url:
            address = (params or {}).get("address")
            body = self.geocodes[address]
            return FakeResponse(body=body)
        radius = int((params or {}).get("radius"))
        body = self.around[radius]
        return FakeResponse(body=body)


def test_search_missing_field():
    response = client.post("/search", json={"city_a": "杭州", "address_a": "杭州东站"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["stage"] == "search"


def test_search_success_mocked():
    data = SearchData(
        search_id="sch_20260906_171500_abc123",
        midpoint=Midpoint(longitude=120.18675, latitude=30.27495),
        pois=[
            {
                "name": "星巴克（庆春路店）",
                "address": "杭州市上城区庆春路123号",
                "distance_to_midpoint_m": 450,
            }
        ],
    )
    with patch("api.search.search_meeting", new_callable=AsyncMock, return_value=data):
        response = client.post(
            "/search",
            json={
                "city_a": "杭州",
                "address_a": "杭州东站",
                "city_b": "杭州",
                "address_b": "西湖龙翔桥地铁站",
                "category": "咖啡店",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["search_id"].startswith("sch_")
    assert body["data"]["midpoint"]["longitude"] == 120.18675
    assert body["data"]["pois"][0]["distance_to_midpoint_m"] == 450
    assert "request_id" in body


def test_midpoint_is_axis_average():
    point_a = amap_search.GeoPoint(*POINT_EAST, "杭州东站", "兴趣点")
    point_b = amap_search.GeoPoint(*POINT_LONGXIANG, "龙翔桥", "兴趣点")
    mid = amap_search.geographic_midpoint(point_a, point_b)
    assert mid.longitude == (POINT_EAST[0] + POINT_LONGXIANG[0]) / 2
    assert mid.latitude == (POINT_EAST[1] + POINT_LONGXIANG[1]) / 2


def test_pick_single_precise_match():
    geocodes = [
        geo_item(*POINT_EAST, "浙江省杭州市上城区杭州东站", level="兴趣点"),
    ]
    point = amap_search.pick_geocode(geocodes, "杭州", "杭州东站")
    assert point.longitude == POINT_EAST[0]
    assert point.latitude == POINT_EAST[1]


def test_pick_accepts_metro_station_level():
    geocodes = [
        geo_item(
            *POINT_LONGXIANG,
            "浙江省杭州市上城区杭州西湖(湖滨店)龙翔桥(地铁站)",
            level="公交地铁站点",
            district="上城区",
        ),
    ]
    point = amap_search.pick_geocode(geocodes, "杭州", "西湖龙翔桥地铁站")
    assert point.latitude == POINT_LONGXIANG[1]


def test_pick_rejects_coarse_level():
    geocodes = [geo_item(120.2, 30.3, "浙江省杭州市", level="区县")]
    with pytest.raises(AppError) as exc:
        amap_search.pick_geocode(geocodes, "杭州", "杭州")
    assert exc.value.code == "GEOCODE_FAILED"


def test_pick_300m_different_places_are_ambiguous():
    geocodes = [
        geo_item(120.2000, 30.2500, "杭州市西湖区市民中心A座", level="兴趣点", district="西湖区"),
        geo_item(120.2000, 30.2518, "杭州市西湖区市民中心B座", level="兴趣点", district="西湖区"),
    ]
    distance = amap_search.haversine_m(120.2000, 30.2500, 120.2000, 30.2518)
    assert 150 < distance < 300
    with pytest.raises(AppError) as exc:
        amap_search.pick_geocode(geocodes, "杭州", "市民中心")
    assert exc.value.code == "AMBIGUOUS_LOCATION"


def test_pick_considers_all_candidates_not_just_first_two():
    geocodes = [
        geo_item(120.2000, 30.2500, "杭州市西湖区市民中心", level="兴趣点", district="西湖区"),
        geo_item(120.20001, 30.25001, "杭州市西湖区市民中心", level="兴趣点", district="西湖区"),
        geo_item(120.2100, 30.2600, "杭州市上城区市民中心", level="兴趣点", district="上城区"),
    ]
    with pytest.raises(AppError) as exc:
        amap_search.pick_geocode(geocodes, "杭州", "市民中心")
    assert exc.value.code == "AMBIGUOUS_LOCATION"


def test_pick_near_duplicates_same_address_are_one_place():
    geocodes = [
        geo_item(120.21250, 30.29090, "浙江省杭州市上城区杭州东站", level="兴趣点"),
        geo_item(120.21251, 30.29091, "浙江省杭州市上城区杭州东站", level="兴趣点"),
    ]
    point = amap_search.pick_geocode(geocodes, "杭州", "杭州东站")
    assert point.formatted_address.endswith("杭州东站")


def test_missing_poi_distance_uses_haversine_not_zero():
    midpoint = Midpoint(longitude=120.0, latitude=30.0)
    pois = [
        poi_item("远店", "杭州市某处1号", 120.02, 30.02),
        poi_item("近店", "杭州市某处2号", 120.001, 30.001, distance="120"),
    ]
    result = amap_search.collect_valid_pois(pois, midpoint)
    assert result[0].name == "近店"
    assert result[0].distance_to_midpoint_m == 120
    far = next(item for item in result if item.name == "远店")
    expected = round(amap_search.haversine_m(120.0, 30.0, 120.02, 30.02))
    assert far.distance_to_midpoint_m == expected
    assert far.distance_to_midpoint_m != 0


@pytest.mark.asyncio
async def test_search_expands_radius_when_first_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "amap-test")
    monkeypatch.setattr(amap_search.settings, "storage_dir", tmp_path)
    fake = FakeClient(
        geocodes={
            "杭州东站": geocode_body(geo_item(*POINT_EAST, "浙江省杭州市上城区杭州东站")),
            "西湖龙翔桥地铁站": geocode_body(
                geo_item(*POINT_LONGXIANG, "浙江省杭州市西湖区龙翔桥地铁站", district="西湖区")
            ),
        },
        around={
            2000: around_body(),
            5000: around_body(
                poi_item("远处咖啡", "杭州市某处88号", 120.186, 30.275, "3200"),
            ),
        },
    )
    monkeypatch.setattr(amap_search.httpx, "AsyncClient", lambda: fake)
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    data = await amap_search.search_meeting(payload)
    assert data.pois[0].name == "远处咖啡"
    saved = next((tmp_path / "searches").glob("*.json"))
    assert '"radius_m": 5000' in saved.read_text(encoding="utf-8")


def test_pois_sorted_by_backend_not_vendor_order():
    midpoint = Midpoint(longitude=120.0, latitude=30.0)
    pois = [
        poi_item("C店", "地址C", 120.0, 30.0, distance="900"),
        poi_item("A店", "地址A", 120.0, 30.0, distance="100"),
        poi_item("B店", "地址B", 120.0, 30.0, distance="400"),
        poi_item("D店", "地址D", 120.0, 30.0, distance="1200"),
    ]
    result = amap_search.collect_valid_pois(pois, midpoint)
    assert [item.name for item in result] == ["A店", "B店", "C店"]
    assert len(result) == 3


@pytest.mark.asyncio
async def test_search_meeting_success_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "amap-test")
    monkeypatch.setattr(amap_search.settings, "storage_dir", tmp_path)
    capture = []
    fake = FakeClient(
        geocodes={
            "杭州东站": geocode_body(geo_item(*POINT_EAST, "浙江省杭州市上城区杭州东站")),
            "西湖龙翔桥地铁站": geocode_body(
                geo_item(*POINT_LONGXIANG, "浙江省杭州市西湖区龙翔桥地铁站", district="西湖区")
            ),
        },
        around={
            2000: around_body(
                poi_item("星巴克（庆春路店）", "杭州市上城区庆春路123号", 120.186, 30.275, "450"),
                poi_item("瑞幸咖啡", "杭州市下城区武林路1号", 120.190, 30.280, "920"),
            )
        },
        capture=capture,
    )
    monkeypatch.setattr(amap_search.httpx, "AsyncClient", lambda: fake)
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    data = await amap_search.search_meeting(payload)
    assert data.search_id.startswith("sch_")
    assert data.midpoint.longitude == (POINT_EAST[0] + POINT_LONGXIANG[0]) / 2
    assert data.pois[0].distance_to_midpoint_m == 450
    saved = next((tmp_path / "searches").glob("*.json"))
    text = saved.read_text(encoding="utf-8")
    assert data.search_id in text
    assert "created_at" in text
    assert "location_a" in text
    timeouts = {item["timeout"] for item in capture}
    assert amap_search.settings.timeout_search_geocode in timeouts
    assert amap_search.settings.timeout_search_poi in timeouts


@pytest.mark.asyncio
async def test_search_expands_radius_then_no_results(tmp_path, monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "amap-test")
    monkeypatch.setattr(amap_search.settings, "storage_dir", tmp_path)
    fake = FakeClient(
        geocodes={
            "杭州东站": geocode_body(geo_item(*POINT_EAST, "浙江省杭州市上城区杭州东站")),
            "西湖龙翔桥地铁站": geocode_body(
                geo_item(*POINT_LONGXIANG, "浙江省杭州市西湖区龙翔桥地铁站", district="西湖区")
            ),
        },
        around={2000: around_body(), 5000: around_body()},
    )
    monkeypatch.setattr(amap_search.httpx, "AsyncClient", lambda: fake)
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="火星补给站",
    )
    with pytest.raises(AppError) as exc:
        await amap_search.search_meeting(payload)
    assert exc.value.code == "NO_RESULTS"
    searches = tmp_path / "searches"
    assert (not searches.exists()) or (not list(searches.glob("*.json")))


@pytest.mark.asyncio
async def test_search_timeout(monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "amap-test")
    fake = FakeClient(error=httpx.TimeoutException("timeout"))
    monkeypatch.setattr(amap_search.httpx, "AsyncClient", lambda: fake)
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    with pytest.raises(AppError) as exc:
        await amap_search.search_meeting(payload)
    assert exc.value.status_code == 504
    assert exc.value.code == "SEARCH_TIMEOUT"


@pytest.mark.asyncio
async def test_search_missing_key(monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "")
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    with pytest.raises(AppError) as exc:
        await amap_search.search_meeting(payload)
    assert exc.value.code == "AMAP_SERVICE_ERROR"


@pytest.mark.asyncio
async def test_search_vendor_error(monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "amap-test")
    fake = FakeClient(
        geocodes={
            "杭州东站": {"status": "0", "infocode": "10001", "geocodes": []},
            "西湖龙翔桥地铁站": {"status": "0", "infocode": "10001", "geocodes": []},
        },
    )
    monkeypatch.setattr(amap_search.httpx, "AsyncClient", lambda: fake)
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    with pytest.raises(AppError) as exc:
        await amap_search.search_meeting(payload)
    assert exc.value.code == "AMAP_SERVICE_ERROR"


@pytest.mark.asyncio
async def test_geocode_engine_error_is_failed_not_502(monkeypatch):
    monkeypatch.setattr(amap_search.settings, "amap_api_key", "amap-test")
    engine = {
        "status": "0",
        "infocode": "30001",
        "info": "ENGINE_RESPONSE_DATA_ERROR",
        "geocodes": [],
    }
    fake = FakeClient(
        geocodes={
            "杭州东站": engine,
            "西湖龙翔桥地铁站": engine,
        }
    )
    monkeypatch.setattr(amap_search.httpx, "AsyncClient", lambda: fake)
    payload = SearchRequest(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    with pytest.raises(AppError) as exc:
        await amap_search.search_meeting(payload)
    assert exc.value.status_code == 422
    assert exc.value.code == "GEOCODE_FAILED"
