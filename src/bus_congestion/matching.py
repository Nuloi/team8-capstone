"""노선 매칭 — 기본번호, 노선번호·정류장명 조회, 미래캠퍼스 노선 판정.

규칙 근거: CLAUDE.md "노선 매칭 규칙", docs/api_bus_route.md
"""

import difflib
import re
from pathlib import Path

import pandas as pd

from bus_congestion.config import Settings
from bus_congestion.storage import ENCODING

BASE_NO_RE = re.compile(r"^\D*?\d+(?:-\d+)?")


def base_no(rte_no: str) -> str:
    """기본번호: '34연세대'→'34', '100-1기업도시'→'100-1', '심야2'→'심야2'. 숫자가 없으면 전체."""
    rte_no = (rte_no or "").strip()
    m = BASE_NO_RE.match(rte_no)
    return m.group() if m else rte_no


def normalize_query(text: str) -> str:
    """사용자 입력 정리: 공백 제거, 끝의 '번' 제거. '34 번' → '34'"""
    text = re.sub(r"\s+", "", text or "")
    return text[:-1] if text.endswith("번") else text


def match_routes(query: str, route_names: dict[str, str]) -> list[str]:
    """노선번호 입력 → 기본번호가 완전히 같은 rte_id 목록. 없으면 비슷한 후보와 함께 LookupError."""
    q = normalize_query(query)
    hits = sorted(rid for rid, no in route_names.items() if base_no(no) == q)
    if not hits:
        bases = sorted({base_no(no) for no in route_names.values()})
        near = difflib.get_close_matches(q, bases, n=5, cutoff=0.5)
        raise LookupError(f"'{query}'에 맞는 노선이 없습니다. 비슷한 번호: {near or '없음'}")
    return hits


def _latest_names(folder: Path, id_col: str, name_col: str) -> dict[str, str]:
    """날짜별 스냅샷을 오래된 것부터 읽어 덮어쓴다 → 같은 ID는 가장 최근 표기가 남는다."""
    names: dict[str, str] = {}
    for path in sorted(folder.glob("*.csv")):
        df = pd.read_csv(path, dtype=str, encoding=ENCODING, keep_default_na=False)
        names.update(zip(df[id_col], df[name_col]))
    return names


def load_route_names(data_dir: Path) -> dict[str, str]:
    """rte_id → rte_no (가장 최근 스냅샷 표기)"""
    return _latest_names(data_dir / "ref" / "routes", "rte_id", "rte_no")


def load_stop_names(data_dir: Path) -> dict[str, str]:
    """sttn_id → sttn_nm (여러 날짜 스냅샷을 합침 — 정류장 목록은 그날 운행한 곳만 나오므로)"""
    return _latest_names(data_dir / "ref" / "stops", "sttn_id", "sttn_nm")


def campus_route_ids(raw_files: list[Path], settings: Settings) -> set[str]:
    """미래캠퍼스 노선 = 캠퍼스 정류장을 지나는 rte_id(수집된 모든 날짜) ∪ 수동 포함 목록."""
    found: set[str] = set()
    for path in raw_files:
        df = pd.read_csv(path, dtype=str, encoding=ENCODING, usecols=["rte_id", "sttn_id"])
        found |= set(df.loc[df["sttn_id"].isin(settings.campus_stop_ids), "rte_id"])
    return found | set(settings.campus_manual_route_ids)
