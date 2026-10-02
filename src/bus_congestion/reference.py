"""노선·정류장 목록 스냅샷.

- 노선: data/ref/routes/{opr_ymd}.csv — ctpv_cd=51로 요청, NO_DATA_FOUND면 42로 재요청.
  sgg_cd가 원주(51130/42130)인 것만 남기고 rte_id 기준 중복 제거 (원본은 모든 행이 2번씩 옴).
  ※ 버스노선 API는 sgg_cd 파라미터를 받지 않는다(넣으면 INVALID_PARAMETER).
- 정류장: data/ref/stops/{opr_ymd}.csv — ctpv_cd=51, sgg_cd=51130 (sgg_cd 필수).
- 공휴일: data/ref/holidays/{연도}.csv — 한국천문연구원 특일정보 getRestDeInfo (대체·임시공휴일 포함).
값은 가공하지 않고 응답 그대로 저장한다.
"""

import json
from pathlib import Path

from bus_congestion.api import BUS_ROUTE, BUS_STOP, ApiClient, ApiError, NoDataError
from bus_congestion.config import Settings
from bus_congestion.storage import (
    HOLIDAY_COLUMNS,
    ROUTE_COLUMNS,
    STOP_COLUMNS,
    check_fields,
    write_csv_atomic,
)


def routes_path(data_dir: Path, ymd: str) -> Path:
    return data_dir / "ref" / "routes" / f"{ymd}.csv"


def stops_path(data_dir: Path, ymd: str) -> Path:
    return data_dir / "ref" / "stops" / f"{ymd}.csv"


def holidays_path(data_dir: Path, year: int) -> Path:
    return data_dir / "ref" / "holidays" / f"{year}.csv"


def parse_holidays(text: str) -> list[dict]:
    """특일정보 응답 해석. 형식: {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [...]}}}}"""
    try:
        data = json.loads(text)
    except ValueError:
        ApiClient._parse(text)  # XML(인증·한도 오류)이면 알맞은 예외를 던진다
        raise
    resp = data.get("response", {})
    header = resp.get("header", {})
    if header.get("resultCode") != "00":
        raise ApiError(f"특일정보 resultCode={header.get('resultCode')} resultMsg={header.get('resultMsg')}")
    body = resp.get("body", {})
    items = body.get("items") or {}  # 결과가 없으면 items가 빈 문자열로 온다
    items = items.get("item", []) if isinstance(items, dict) else []
    items = [items] if isinstance(items, dict) else list(items)
    if len(items) != int(body.get("totalCount", 0)):
        raise ApiError(f"받은 건수 {len(items)}가 totalCount {body.get('totalCount')}와 다릅니다.")
    return [{**it, "locdate": str(it["locdate"]), "seq": str(it["seq"])} for it in items]


def snapshot_holidays(client: ApiClient, settings: Settings, data_dir: Path, year: int, overwrite: bool = False) -> str:
    """연도 전체 공휴일을 한 번에 받는다(1회 호출). 반환값: saved / skipped / no_data"""
    out = holidays_path(data_dir, year)
    if out.exists() and not overwrite:
        return "skipped"
    text = client.request_external(settings["holiday"]["url"],
                                   {"solYear": str(year), "numOfRows": 100, "_type": "json"})
    items = parse_holidays(text)
    if not items:
        return "no_data"
    check_fields(items, HOLIDAY_COLUMNS)
    write_csv_atomic(out, HOLIDAY_COLUMNS, sorted(items, key=lambda r: (r["locdate"], r["seq"])))
    return "saved"


def snapshot_routes(client: ApiClient, settings: Settings, data_dir: Path, ymd: str, overwrite: bool = False) -> str:
    """반환값: saved / skipped / no_data"""
    out = routes_path(data_dir, ymd)
    if out.exists() and not overwrite:
        return "skipped"
    region = settings["region"]
    items = None
    for ctpv in (region["ctpv_cd"], region["route_ctpv_fallback"]):
        try:
            items = client.fetch_all(BUS_ROUTE, {"opr_ymd": ymd, "ctpv_cd": ctpv})
            break
        except NoDataError:
            continue
    if items is None:
        return "no_data"

    wanted = set(region["route_sgg_cds"])
    unique = {}
    for item in items:
        if item.get("sgg_cd") in wanted:
            unique.setdefault(item["rte_id"], item)
    rows = sorted(unique.values(), key=lambda r: r["rte_id"])
    if not rows:
        return "no_data"
    check_fields(rows, ROUTE_COLUMNS)
    write_csv_atomic(out, ROUTE_COLUMNS, rows)
    return "saved"


def snapshot_stops(client: ApiClient, settings: Settings, data_dir: Path, ymd: str, overwrite: bool = False) -> str:
    """반환값: saved / skipped / no_data"""
    out = stops_path(data_dir, ymd)
    if out.exists() and not overwrite:
        return "skipped"
    region = settings["region"]
    try:
        items = client.fetch_all(BUS_STOP, {"opr_ymd": ymd, "ctpv_cd": region["ctpv_cd"], "sgg_cd": region["sgg_cd"]})
    except NoDataError:
        return "no_data"
    check_fields(items, STOP_COLUMNS)
    write_csv_atomic(out, STOP_COLUMNS, sorted(items, key=lambda r: r["sttn_id"]))
    return "saved"
