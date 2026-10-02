"""행 단위 정제: data/raw/{sgg}/{ymd}.csv → data/clean/{sgg}/{ymd}.csv

규칙 근거: CLAUDE.md "정제 규칙"
- 미래캠퍼스 노선만 남김
- 제거: 완전 중복 행, 분석 제외 노선(고장·공차·조조), tzon 00~23 밖
- 유지: 같은 노선·회차·정류장·시간대인데 cgst만 다른 행 (별개 관측값)
- 추가: rte_no, base_no, sttn_nm, est_pax, is_holiday
raw는 절대 수정하지 않는다. clean은 언제든 raw에서 다시 만든다(항상 덮어씀).
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from bus_congestion.config import Settings
from bus_congestion.matching import base_no, campus_route_ids, load_route_names, load_stop_names
from bus_congestion.storage import CONGESTION_COLUMNS, ENCODING

CLEAN_COLUMNS = CONGESTION_COLUMNS + ["rte_no", "base_no", "sttn_nm", "est_pax", "is_holiday"]
INT_COLUMNS = ["opr_trntm", "sttn_seq", "cgst"]


class CleanError(Exception):
    pass


@dataclass
class CleanContext:
    """하루치 정제에 필요한 참조 정보 (연도 단위로 한 번 만든다)."""
    campus_ids: set[str]
    route_names: dict[str, str]
    stop_names: dict[str, str]
    holidays: set[str]
    excluded_route_nos: frozenset[str]
    tzon_min: int
    tzon_max: int
    pax_capacity: int


@dataclass
class DayStats:
    ymd: str
    raw_rows: int = 0
    duplicates: int = 0
    non_campus: int = 0
    excluded: int = 0
    bad_tzon: int = 0
    clean_rows: int = 0
    missing_route_name: set[str] = field(default_factory=set)


def raw_files(data_dir: Path, sgg_cd: str, year: int) -> list[Path]:
    return sorted((data_dir / "raw" / sgg_cd).glob(f"{year}[0-9][0-9][0-9][0-9].csv"))


def clean_dir(data_dir: Path, sgg_cd: str) -> Path:
    return data_dir / "clean" / sgg_cd


def load_holidays(data_dir: Path, year: int) -> set[str]:
    path = data_dir / "ref" / "holidays" / f"{year}.csv"
    if not path.exists():
        raise CleanError(f"공휴일 목록이 없습니다: {path} — 먼저 `python -m bus_congestion holidays --year {year}` 실행")
    df = pd.read_csv(path, dtype=str, encoding=ENCODING, keep_default_na=False)
    return set(df.loc[df["isHoliday"] == "Y", "locdate"])


def build_context(settings: Settings, data_dir: Path, year: int, files: list[Path]) -> CleanContext:
    route_names = load_route_names(data_dir)
    if not route_names:
        raise CleanError("노선 목록 스냅샷이 없습니다 — 먼저 `python -m bus_congestion reference ...` 실행")
    return CleanContext(
        campus_ids=campus_route_ids(files, settings),
        route_names=route_names,
        stop_names=load_stop_names(data_dir),
        holidays=load_holidays(data_dir, year),
        excluded_route_nos=settings.excluded_route_nos,
        tzon_min=settings["clean"]["valid_tzon_min"],
        tzon_max=settings["clean"]["valid_tzon_max"],
        pax_capacity=settings["clean"]["pax_capacity"],
    )


def read_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, encoding=ENCODING, keep_default_na=False)
    if list(df.columns) != CONGESTION_COLUMNS:
        raise CleanError(f"{path.name}: raw 컬럼이 스키마와 다릅니다.")
    return df


def clean_frame(df: pd.DataFrame, ctx: CleanContext, ymd: str = "") -> tuple[pd.DataFrame, DayStats]:
    st = DayStats(ymd=ymd, raw_rows=len(df))

    before = len(df)
    df = df.drop_duplicates(subset=CONGESTION_COLUMNS)
    st.duplicates = before - len(df)

    before = len(df)
    df = df[df["rte_id"].isin(ctx.campus_ids)]
    st.non_campus = before - len(df)

    rte_no = df["rte_id"].map(ctx.route_names)
    st.missing_route_name = set(df.loc[rte_no.isna(), "rte_id"])
    rte_no = rte_no.fillna("")
    before = len(df)
    keep = ~rte_no.isin(ctx.excluded_route_nos)
    df, rte_no = df[keep], rte_no[keep]
    st.excluded = before - len(df)

    tz = pd.to_numeric(df["tzon"], errors="coerce")
    keep = tz.between(ctx.tzon_min, ctx.tzon_max) & (df["tzon"].str.len() == 2)
    st.bad_tzon = int((~keep).sum())
    df, rte_no = df[keep].copy(), rte_no[keep]

    for c in INT_COLUMNS:
        df[c] = df[c].astype(int)
    df["rte_no"] = rte_no
    df["base_no"] = rte_no.map(base_no)
    df["sttn_nm"] = df["sttn_id"].map(ctx.stop_names).fillna("")
    # 반올림은 사사오입 (파이썬 round의 은행가 반올림을 피함)
    df["est_pax"] = (df["cgst"] * ctx.pax_capacity / 100 + 0.5).astype(int)
    df["is_holiday"] = df["opr_ymd"].isin(ctx.holidays).map({True: "Y", False: "N"})
    st.clean_rows = len(df)
    return df[CLEAN_COLUMNS], st


def clean_year(settings: Settings, data_dir: Path, year: int) -> list[DayStats]:
    sgg = settings["region"]["sgg_cd"]
    files = raw_files(data_dir, sgg, year)
    if not files:
        raise CleanError(f"{year}년 raw 파일이 없습니다: {data_dir / 'raw' / sgg}")
    ctx = build_context(settings, data_dir, year, files)
    out_dir = clean_dir(data_dir, sgg)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = []
    for path in files:
        df, st = clean_frame(read_raw(path), ctx, path.stem)
        tmp = out_dir / f"{path.stem}.csv.tmp"
        df.to_csv(tmp, index=False, encoding=ENCODING)
        tmp.replace(out_dir / f"{path.stem}.csv")
        stats.append(st)
    return stats
