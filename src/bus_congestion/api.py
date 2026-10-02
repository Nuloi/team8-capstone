"""공공데이터포털(1613000) API 공통 호출.

세 API(노선별 혼잡도·버스노선·버스정류장)가 같은 응답 구조를 쓴다:
    성공: {"Response": {"header": {...}, "body": {"items": {"item": [...]}, "totalCount": N}}}
    오류: {"Error": {"code": "50", "message": "NO_DATA_FOUND"}}   ← HTTP 200으로 온다
인증·트래픽 오류는 JSON이 아니라 XML(OpenAPI_ServiceResponse)로 올 수 있다.
"""

import json
import math
import re
import time

import requests

from bus_congestion.config import Settings

CONGESTION = "RouteCongestionLevel/getRouteCongestionLevel"
BUS_ROUTE = "BusRoute/getBusRoute"
BUS_STOP = "BusStop/getBusStop"

# 공공데이터포털 공통 오류 코드 (XML 응답의 returnReasonCode)
QUOTA_CODES = {"22"}  # LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR
AUTH_CODES = {"20", "30", "31", "32", "33"}  # 접근거부·미등록키·기한만료·미등록IP·미서명


class ApiError(Exception):
    """재시도해도 해결되지 않는 API 오류."""


class NoDataError(ApiError):
    """조건에 맞는 데이터가 없음 (NO_DATA_FOUND). 재시도하지 않는다."""


class QuotaExceededError(ApiError):
    """일일 호출 한도 초과. 오늘 수집을 멈춰야 한다."""


class CallBudgetExceededError(ApiError):
    """--max-calls로 정한 호출 상한에 도달."""


class ApiClient:
    def __init__(self, settings: Settings, service_key: str, session=None, max_calls: int | None = None):
        self.base_url = settings["api"]["base_url"].rstrip("/")
        self.num_of_rows = settings["api"]["num_of_rows"]
        self.timeout = settings["api"]["timeout_sec"]
        self.interval = settings["request"]["interval_sec"]
        self.max_retries = settings["request"]["max_retries"]
        self.backoff = settings["request"]["backoff_sec"]
        self._key = service_key
        self._session = session or requests.Session()
        self._last_call = 0.0
        self.max_calls = max_calls
        self.calls = 0  # 실제로 보낸 요청 수 (재시도 포함) — 일일 한도 계산용

    # ---- 저수준 ----------------------------------------------------------
    def _wait_interval(self):
        gap = time.monotonic() - self._last_call
        if gap < self.interval:
            time.sleep(self.interval - gap)

    def _request(self, path: str, params: dict) -> str:
        """1613000(혼잡도·노선·정류장) API 요청."""
        return self.request_url(f"{self.base_url}/{path}", {"serviceKey": self._key, "dataType": "JSON", **params})

    def request_external(self, url: str, params: dict, key_param: str = "ServiceKey") -> str:
        """다른 기관 API(예: 특일정보) 요청 — 키 이름·응답 형식이 다르다. 본문 해석은 호출한 쪽에서 한다."""
        return self.request_url(url, {key_param: self._key, **params})

    def request_url(self, url: str, full: dict) -> str:
        """한 번의 HTTP 요청. 네트워크 오류·5xx는 재시도한다. 호출 수·간격·상한을 관리한다."""
        for attempt in range(1, self.max_retries + 2):  # 최초 1회 + 재시도
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise CallBudgetExceededError(f"호출 상한 {self.max_calls}회에 도달했습니다.")
            self._wait_interval()
            self.calls += 1
            try:
                resp = self._session.get(url, params=full, timeout=self.timeout)
                self._last_call = time.monotonic()
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                return resp.text
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                self._last_call = time.monotonic()
                if attempt > self.max_retries:
                    raise ApiError(f"네트워크 오류 (재시도 {self.max_retries}회 실패): {e}") from None
                time.sleep(self.backoff * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(text: str) -> dict:
        """응답 본문을 해석해 body를 돌려준다. 오류면 알맞은 예외를 던진다."""
        try:
            data = json.loads(text)
        except ValueError:
            # 인증·트래픽 오류는 XML로 온다
            code = re.search(r"<returnReasonCode>(\d+)</returnReasonCode>", text)
            msg = re.search(r"<returnAuthMsg>([^<]*)</returnAuthMsg>", text)
            code, msg = (code.group(1) if code else "?"), (msg.group(1) if msg else text[:200])
            if code in QUOTA_CODES:
                raise QuotaExceededError(f"일일 호출 한도 초과 ({msg})") from None
            if code in AUTH_CODES:
                raise ApiError(f"인증 오류 ({msg}) — .env의 키가 Decoding 키인지, 활용신청이 승인됐는지 확인") from None
            raise ApiError(f"알 수 없는 응답 (code={code}): {msg}") from None

        if "Error" in data:
            err = data["Error"]
            if err.get("code") == "50":
                raise NoDataError(err.get("message", "NO_DATA_FOUND"))
            if err.get("code") in QUOTA_CODES:
                raise QuotaExceededError(err.get("message", ""))
            raise ApiError(f"API 오류 code={err.get('code')} message={err.get('message')}")

        resp = data.get("Response", {})
        header = resp.get("header", {})
        if header.get("resultCode") != "200":
            raise ApiError(f"resultCode={header.get('resultCode')} resultMsg={header.get('resultMsg')}")
        return resp.get("body", {})

    @staticmethod
    def _items(body: dict) -> list[dict]:
        items = (body.get("items") or {}).get("item") or []
        return [items] if isinstance(items, dict) else list(items)  # 1건이면 객체로 올 수 있음

    # ---- 고수준 ----------------------------------------------------------
    def fetch_all(self, path: str, params: dict) -> list[dict]:
        """모든 페이지를 받아 item 목록을 돌려준다.

        받은 건수가 totalCount와 다르면 ApiError (반쪽 데이터를 저장하지 않기 위해).
        """
        body = self._parse(self._request(path, {**params, "pageNo": 1, "numOfRows": self.num_of_rows}))
        total = int(body.get("totalCount", 0))
        items = self._items(body)
        pages = math.ceil(total / self.num_of_rows) if total else 1
        for page in range(2, pages + 1):
            body = self._parse(self._request(path, {**params, "pageNo": page, "numOfRows": self.num_of_rows}))
            items.extend(self._items(body))
        if len(items) != total:
            raise ApiError(f"받은 건수 {len(items)}가 totalCount {total}와 다릅니다.")
        return items
