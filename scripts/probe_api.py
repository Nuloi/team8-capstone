"""노선별 혼잡도 API 1회 점검 스크립트.

작은 범위로 한 번 호출해 응답 구조·필드·타입이 docs/api_spec.md와 맞는지 확인한다.
대량 수집용이 아니다. 표준 라이브러리만 사용하므로 가상환경 없이도 실행된다.

사용 예:
    python scripts/probe_api.py --date 20260101
    python scripts/probe_api.py --date 20260101 --rte 43700100 --rows 5
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

URL = "https://apis.data.go.kr/1613000/RouteCongestionLevel/getRouteCongestionLevel"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# docs/api_spec.md 4.3 기준 필드와 실제 타입
EXPECTED_FIELDS = {
    "opr_ymd": str,
    "dow_cd": str,
    "dow_nm": str,
    "ctpv_cd": str,
    "ctpv_nm": str,
    "sgg_cd": str,
    "sgg_nm": str,
    "emd_cd": str,
    "emd_nm": str,
    "rte_id": str,
    "opr_trntm": int,
    "sttn_seq": int,
    "sttn_id": str,
    "trfc_mns_se_cd": str,
    "tzon": str,
    "cgst": int,
}


def load_service_key():
    """.env에서 SERVICE_KEY를 읽는다. 값은 절대 출력하지 않는다."""
    if not ENV_PATH.exists():
        sys.exit(f"[오류] {ENV_PATH} 파일이 없습니다. .env.example을 복사해 만드세요.")
    for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("SERVICE_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    sys.exit("[오류] .env에 SERVICE_KEY 값이 없습니다. 형식: SERVICE_KEY=키값")


def parse_args():
    p = argparse.ArgumentParser(description="노선별 혼잡도 API 1회 점검")
    p.add_argument("--date", required=True, help="운행일자 YYYYMMDD")
    p.add_argument("--ctpv", default="51", help="시도코드 (기본: 51 강원특별자치도)")
    p.add_argument("--sgg", default="51130", help="시군구코드 (기본: 51130 원주시)")
    p.add_argument("--rte", help="노선아이디 rte_id (선택)")
    p.add_argument("--sttn", help="정류장아이디 sttn_id (선택)")
    p.add_argument("--rows", type=int, default=3, help="받을 행 수 (1~10, 기본 3)")
    args = p.parse_args()
    if not 1 <= args.rows <= 10:
        p.error("--rows는 1~10 사이로 지정하세요 (점검용).")
    return args


def call_api(key, args):
    params = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": args.rows,
        "opr_ymd": args.date,
        "ctpv_cd": args.ctpv,
        "sgg_cd": args.sgg,
        "dataType": "JSON",
    }
    if args.rte:
        params["rte_id"] = args.rte
    if args.sttn:
        params["sttn_id"] = args.sttn

    shown = {k: ("***" if k == "serviceKey" else v) for k, v in params.items()}
    print(f"[요청] {shown}")

    req = URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        sys.exit(f"[오류] 네트워크: {e}")
    return status, body.replace(key, "***")


def check_items(items):
    """필드 누락·추가·타입 불일치를 보고한다. 문제 수를 반환한다."""
    problems = 0
    for i, item in enumerate(items, 1):
        missing = EXPECTED_FIELDS.keys() - item.keys()
        extra = item.keys() - EXPECTED_FIELDS.keys()
        wrong_type = [
            f"{k}({type(item[k]).__name__}, 기대 {t.__name__})"
            for k, t in EXPECTED_FIELDS.items()
            if k in item and not isinstance(item[k], t)
        ]
        for label, values in (("누락", missing), ("명세에 없음", extra), ("타입 다름", wrong_type)):
            if values:
                problems += 1
                print(f"  - {i}번째 행 {label}: {', '.join(sorted(values))}")
    return problems


def main():
    args = parse_args()
    key = load_service_key()
    status, body = call_api(key, args)
    print(f"[HTTP] {status}")

    try:
        data = json.loads(body)
    except ValueError:
        print("[오류] JSON이 아닌 응답:")
        print(body[:1000])
        return 1

    # 오류도 HTTP 200으로 온다 → 본문으로 판단
    if "Error" in data:
        err = data["Error"]
        print(f"[API 오류] code={err.get('code')} message={err.get('message')}")
        return 1

    resp = data.get("Response", {})
    header, body_ = resp.get("header", {}), resp.get("body", {})
    print(f"[결과] resultCode={header.get('resultCode')} resultMsg={header.get('resultMsg')}")
    if header.get("resultCode") != "200":
        return 1

    items = body_.get("items", {}).get("item", [])
    if isinstance(items, dict):  # 1건일 때 객체로 올 가능성 대비
        print("[참고] item이 리스트가 아닌 객체로 왔습니다.")
        items = [items]

    print(f"[건수] totalCount={body_.get('totalCount')} / 이번 응답 {len(items)}건")
    print(f"[구조] Response.body 키: {sorted(body_.keys())}")

    print("[필드 점검]")
    problems = check_items(items)
    if problems == 0:
        print("  - 명세와 일치")

    print("[샘플]")
    for item in items:
        print("  " + json.dumps(item, ensure_ascii=False))

    return 0 if problems == 0 else 3  # 2는 argparse 인자 오류가 사용


if __name__ == "__main__":
    sys.exit(main())
