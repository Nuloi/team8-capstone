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


def stop_directions(df: pd.DataFrame) -> pd.DataFrame:
    """노선(rte_id)별 정류장의 상행/하행을 정한다. 반환: rte_id, sttn_id, n_obs, direction

    원주 노선은 대부분 왕복형(기점 → 회차 → 기점)이라 같은 이름의 정류장이 갈 때·올 때 한 번씩 나온다.
    이름이 같은 정류장 쌍의 중간 순서들의 중앙값을 회차 지점으로 보고,
    회차 지점 이하(기점 → 회차)는 상행, 이후(회차 → 기점)는 하행으로 둔다.
    쌍이 없는 노선은 순서 범위의 가운데를 회차 지점으로 쓴다.
    """
    s = (df.groupby(["rte_id", "sttn_id"])
           .agg(seq=("sttn_seq", "median"), sttn_nm=("sttn_nm", "last"), n_obs=("sttn_seq", "size"))
           .reset_index())
    turn = {}
    for rid, g in s.groupby("rte_id"):
        named = g[g["sttn_nm"] != ""]
        pairs = named.groupby("sttn_nm")["seq"].agg(["min", "max", "size"])
        pairs = pairs[pairs["size"] >= 2]
        if len(pairs):
            turn[rid] = float(((pairs["min"] + pairs["max"]) / 2).median())
        else:
            turn[rid] = float((g["seq"].min() + g["seq"].max()) / 2)
    s["direction"] = (s["seq"] <= s["rte_id"].map(turn)).map({True: "상행", False: "하행"})
    return s[["rte_id", "sttn_id", "n_obs", "direction"]]


def base_stop_directions(directions: pd.DataFrame) -> pd.Series:
    """기본번호 단위(sttn_id 하나)의 방향: 변형마다 다르면 관측이 많은 쪽, 같으면 상행."""
    w = directions.groupby(["sttn_id", "direction"])["n_obs"].sum().unstack(fill_value=0)
    up = w["상행"] if "상행" in w else 0
    down = w["하행"] if "하행" in w else 0
    return pd.Series((up >= down), index=w.index).map({True: "상행", False: "하행"})


def stop_label(name: str, sttn_id: str, direction: str) -> str:
    """표시용 정류장명: '원주의료원(상행)'. 이름이 없으면 정류장 ID를 쓴다."""
    return f"{name or sttn_id}({direction})"


def stop_labels(stops: pd.DataFrame) -> pd.Series:
    """stops(sttn_id, sttn_nm, direction) → sttn_id별 표시명.

    상행·하행으로도 같은 이름이 남으면(회차 지점 근처·갈래 노선) 정류장 ID 뒤 4자리를 덧붙인다: '구억동(상행·0241)'
    """
    s = stops.drop_duplicates("sttn_id").copy()
    s["label"] = [stop_label(n, i, d) for n, i, d in zip(s["sttn_nm"], s["sttn_id"], s["direction"])]
    clash = s["label"].duplicated(keep=False)
    s.loc[clash, "label"] = [f"{n or i}({d}·{i[-4:]})" for n, i, d in
                             zip(s.loc[clash, "sttn_nm"], s.loc[clash, "sttn_id"], s.loc[clash, "direction"])]
    return s.set_index("sttn_id")["label"]


def campus_route_ids(raw_files: list[Path], settings: Settings) -> set[str]:
    """미래캠퍼스 노선 = 캠퍼스 정류장을 지나는 rte_id(수집된 모든 날짜) ∪ 수동 포함 목록."""
    found: set[str] = set()
    for path in raw_files:
        df = pd.read_csv(path, dtype=str, encoding=ENCODING, usecols=["rte_id", "sttn_id"])
        found |= set(df.loc[df["sttn_id"].isin(settings.campus_stop_ids), "rte_id"])
    return found | set(settings.campus_manual_route_ids)
