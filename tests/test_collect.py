"""수집 코드 테스트 — 실제 API를 부르지 않고 가짜 세션으로 응답을 흉내 낸다."""

import copy
import csv
import json
from datetime import date

import pytest
import requests

from bus_congestion.api import ApiClient, ApiError, NoDataError, QuotaExceededError, CallBudgetExceededError
from bus_congestion.collect import collect_day, collect_range
from bus_congestion.config import Settings, load_settings
from bus_congestion.reference import parse_holidays, snapshot_holidays, snapshot_routes
from bus_congestion.storage import CONGESTION_COLUMNS

QUOTA_XML = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
             "<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>"
             "<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
NO_DATA = json.dumps({"Error": {"code": "50", "message": "NO_DATA_FOUND"}})


def item(i=0, **over):
    base = {"opr_ymd": "20250415", "dow_cd": "3", "dow_nm": "화요일", "ctpv_cd": "51", "ctpv_nm": "강원특별자치도",
            "sgg_cd": "51130", "sgg_nm": "원주시", "emd_cd": "5113010100", "emd_nm": "중앙동",
            "rte_id": "43701901", "opr_trntm": 0, "sttn_seq": i, "sttn_id": "0374311", "trfc_mns_se_cd": "B",
            "tzon": "09", "cgst": 13}
    return {**base, **over}


def page(items, total):
    return json.dumps({"Response": {"header": {"resultCode": "200", "resultMsg": "SUCCESS"},
                                    "body": {"items": {"item": items}, "totalCount": total}}}, ensure_ascii=False)


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeSession:
    """응답(문자열·예외)을 순서대로 돌려준다. 요청 params를 기록한다."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.requests.append((url, dict(params)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, FakeResp) else FakeResp(r)


@pytest.fixture
def settings():
    s = load_settings()
    raw = copy.deepcopy(s.raw)
    raw["request"].update(interval_sec=0, backoff_sec=0)
    raw["api"]["num_of_rows"] = 2  # 페이지 넘김을 작은 숫자로 확인
    return Settings(raw, s.campus_stop_ids, s.campus_manual_route_ids, s.excluded_route_nos)


def client(settings, *responses, **kw):
    return ApiClient(settings, "TEST_KEY", session=FakeSession(*responses), **kw)


# ---- api ---------------------------------------------------------------
def test_fetch_all_paginates(settings):
    c = client(settings, page([item(0), item(1)], 5), page([item(2), item(3)], 5), page([item(4)], 5))
    items = c.fetch_all("X", {"opr_ymd": "20250415"})
    assert [i["sttn_seq"] for i in items] == [0, 1, 2, 3, 4]
    assert c.calls == 3
    assert [p["pageNo"] for _, p in c._session.requests] == [1, 2, 3]
    assert c._session.requests[0][1]["serviceKey"] == "TEST_KEY"  # 키는 params로 (자동 인코딩)


def test_single_item_as_object(settings):
    c = client(settings, page(item(0), 1))
    assert len(c.fetch_all("X", {})) == 1


def test_no_data_raises(settings):
    with pytest.raises(NoDataError):
        client(settings, NO_DATA).fetch_all("X", {})


def test_quota_xml_raises(settings):
    with pytest.raises(QuotaExceededError):
        client(settings, QUOTA_XML).fetch_all("X", {})


def test_retry_on_timeout_then_success(settings):
    c = client(settings, requests.Timeout("t"), FakeResp("", 503), page([item(0)], 1))
    assert len(c.fetch_all("X", {})) == 1
    assert c.calls == 3


def test_retry_gives_up(settings):
    c = client(settings, *[requests.ConnectionError("x")] * 4)
    with pytest.raises(ApiError, match="재시도"):
        c.fetch_all("X", {})


def test_count_mismatch_raises(settings):
    c = client(settings, page([item(0), item(1)], 3), page([], 3))
    with pytest.raises(ApiError, match="totalCount"):
        c.fetch_all("X", {})


def test_call_budget(settings):
    c = client(settings, page([item(0), item(1)], 3), page([item(2)], 3), max_calls=1)
    with pytest.raises(CallBudgetExceededError):
        c.fetch_all("X", {})


# ---- collect -----------------------------------------------------------
def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_collect_day_writes_csv_and_keeps_strings(settings, tmp_path):
    c = client(settings, page([item(0), item(1, extra_field="x")], 2))
    assert collect_day(c, settings, tmp_path, "20250415") == "saved"
    out = tmp_path / "raw" / "51130" / "20250415.csv"
    rows = read_csv(out)
    assert list(rows[0].keys()) == CONGESTION_COLUMNS  # 스키마 순서 고정, 추가 필드는 저장 안 함
    assert rows[0]["tzon"] == "09" and rows[0]["sttn_id"] == "0374311"  # 앞자리 0 유지
    assert not out.with_name(out.name + ".tmp").exists()
    log = read_csv(tmp_path / "raw" / "51130" / "_collect_log.csv")
    assert log[0]["status"] == "saved" and log[0]["rows"] == "2" and "extra_field" in log[0]["message"]


def test_collect_day_skips_existing(settings, tmp_path):
    c = client(settings, page([item(0)], 1))
    collect_day(c, settings, tmp_path, "20250415")
    assert collect_day(c, settings, tmp_path, "20250415") == "skipped"
    assert c.calls == 1


def test_collect_day_no_data_logged(settings, tmp_path):
    assert collect_day(client(settings, NO_DATA), settings, tmp_path, "20240101") == "no_data"
    assert not (tmp_path / "raw" / "51130" / "20240101.csv").exists()
    assert read_csv(tmp_path / "raw" / "51130" / "_collect_log.csv")[0]["status"] == "no_data"


def test_collect_day_missing_field_not_saved(settings, tmp_path):
    bad = item(0)
    del bad["cgst"]
    with pytest.raises(Exception, match="cgst"):
        collect_day(client(settings, page([bad], 1)), settings, tmp_path, "20250415")
    assert not (tmp_path / "raw" / "51130" / "20250415.csv").exists()


def test_collect_range_stops_on_quota_and_resumes(settings, tmp_path):
    c = client(settings, page([item(0)], 1), QUOTA_XML)
    s = collect_range(c, settings, tmp_path, date(2025, 1, 1), date(2025, 1, 3))
    assert s.saved == ["20250101"] and s.stopped_at == "20250102"
    # 다음 날 다시 실행: 받은 날은 건너뛰고 이어서
    c2 = client(settings, page([item(0)], 1), page([item(0)], 1))
    s2 = collect_range(c2, settings, tmp_path, date(2025, 1, 1), date(2025, 1, 3))
    assert s2.skipped == ["20250101"] and s2.saved == ["20250102", "20250103"]


# ---- holidays ----------------------------------------------------------
def holiday_resp(items, total=None):
    body = {"items": {"item": items} if items else "", "numOfRows": 100, "pageNo": 1,
            "totalCount": len(items) if total is None else total}
    return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": body}},
                      ensure_ascii=False)


def test_snapshot_holidays(settings, tmp_path):
    items = [{"dateKind": "01", "dateName": "어린이날", "isHoliday": "Y", "locdate": 20250505, "seq": 1},
             {"dateKind": "01", "dateName": "부처님오신날", "isHoliday": "Y", "locdate": 20250505, "seq": 2}]
    c = client(settings, holiday_resp(items))
    assert snapshot_holidays(c, settings, tmp_path, 2025) == "saved"
    url, params = c._session.requests[0]
    assert "SpcdeInfoService/getRestDeInfo" in url and params["ServiceKey"] == "TEST_KEY"
    assert params["solYear"] == "2025" and params["_type"] == "json"
    rows = read_csv(tmp_path / "ref" / "holidays" / "2025.csv")
    assert [(r["locdate"], r["seq"]) for r in rows] == [("20250505", "1"), ("20250505", "2")]


def test_holidays_empty_and_single():
    assert parse_holidays(holiday_resp([])) == []
    one = {"dateKind": "01", "dateName": "광복절", "isHoliday": "Y", "locdate": 20250815, "seq": 1}
    assert parse_holidays(holiday_resp(one, total=1))[0]["locdate"] == "20250815"


def test_holidays_quota_xml():
    with pytest.raises(QuotaExceededError):
        parse_holidays(QUOTA_XML)


# ---- reference ---------------------------------------------------------
def route(rte_id, sgg="42130", rte_no="34연세대"):
    return {"opr_ymd": "20250415", "ctpv_cd": sgg[:2], "sgg_cd": sgg, "rte_id": rte_id, "rte_no": rte_no,
            "rte_nm": "a-b", "dptre_sttn_id": "1", "dptre_sttn_nm": "a", "arvl_sttn_id": "2", "arvl_sttn_nm": "b"}


def test_snapshot_routes_falls_back_filters_and_dedupes(settings, tmp_path):
    c = client(settings, NO_DATA,  # ctpv 51 → 데이터 없음
               page([route("43701901"), route("43701901")], 3),  # ctpv 42, 중복
               page([route("29022190", sgg="42830")], 3))  # 다른 시군구
    assert snapshot_routes(c, settings, tmp_path, "20250415") == "saved"
    assert c._session.requests[1][1]["ctpv_cd"] == "42"
    assert "sgg_cd" not in c._session.requests[0][1]  # 버스노선 API는 sgg_cd를 받지 않음
    rows = read_csv(tmp_path / "ref" / "routes" / "20250415.csv")
    assert [r["rte_id"] for r in rows] == ["43701901"]
