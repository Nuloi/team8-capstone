"""CSV 저장·로그 공통 함수."""

import csv
import os
from datetime import datetime
from pathlib import Path

ENCODING = "utf-8-sig"  # 팀원이 엑셀로 열 때 한글 깨짐 방지

# 컬럼 순서 = 팀과의 스키마 계약 (CLAUDE.md "수집 규칙"). 바꿀 때는 사용자 확인.
CONGESTION_COLUMNS = [
    "opr_ymd", "dow_cd", "dow_nm", "ctpv_cd", "ctpv_nm", "sgg_cd", "sgg_nm", "emd_cd", "emd_nm",
    "rte_id", "opr_trntm", "sttn_seq", "sttn_id", "trfc_mns_se_cd", "tzon", "cgst",
]
ROUTE_COLUMNS = [
    "opr_ymd", "ctpv_cd", "sgg_cd", "rte_id", "rte_no", "rte_nm",
    "dptre_sttn_id", "dptre_sttn_nm", "arvl_sttn_id", "arvl_sttn_nm",
]
STOP_COLUMNS = [
    "opr_ymd", "sttn_id", "sttn_nm", "sttn_ars_no", "ctpv_cd", "sgg_cd", "emd_cd",
    "ctpv_nm", "sgg_nm", "emd_nm",
]


HOLIDAY_COLUMNS = ["locdate", "seq", "dateKind", "isHoliday", "dateName"]  # 특일정보 API 응답 그대로


class SchemaError(Exception):
    """응답에 필요한 필드가 없음."""


def check_fields(items: list[dict], columns: list[str]) -> set[str]:
    """필수 필드가 빠졌으면 SchemaError. 명세에 없는 추가 필드 이름을 돌려준다(저장하지 않음)."""
    extra = set()
    for i, item in enumerate(items):
        missing = [c for c in columns if c not in item]
        if missing:
            raise SchemaError(f"{i + 1}번째 행에 필드 없음: {missing}")
        extra |= item.keys() - set(columns)
    return extra


def write_csv_atomic(path: Path, columns: list[str], rows: list[dict]) -> None:
    """임시 파일에 다 쓴 뒤 이름을 바꾼다 — 중간에 끊겨도 반쪽 파일이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding=ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def append_log(path: Path, row: dict) -> None:
    """수집 로그(CSV)에 한 줄 추가."""
    columns = ["opr_ymd", "status", "rows", "calls", "message", "logged_at"]
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding=ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerow({**{c: "" for c in columns}, **row, "logged_at": datetime.now().isoformat(timespec="seconds")})
