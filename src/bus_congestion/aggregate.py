"""집계표: data/clean/{sgg}/{year}*.csv → data/agg/{sgg}/{year}/*.csv

규칙 근거: CLAUDE.md "집계표"
- 날짜 단위 3종: 요일별(dow) · 일자 유형별(daytype: 평일/토요일/일요일·공휴일) · 월별(month)
- 노선 단위 2종: 기본번호(base) · 변형 rte_id(route)
- 묶는 기준: 날짜 단위 × 노선 × 정류장 × 시간대
  - route 단위: rte_id, sttn_seq, sttn_id (같은 정류장이 순환 노선에서 두 번 나올 수 있어 순서 포함)
  - base 단위: base_no, sttn_id (변형마다 정류장 순서가 달라 순서는 묶지 않음)
- 지표: n(관측 수), n_days(관측된 날 수), cgst_mean/max/p50/p90, congested_ratio(기준값이 정해진 경우만)
- 기본번호 값은 변형별 평균의 평균이 아니라 행 단위 데이터에서 직접 계산한다.
"""

from pathlib import Path

import pandas as pd

from bus_congestion.clean import CLEAN_COLUMNS, clean_dir
from bus_congestion.config import Settings
from bus_congestion.storage import ENCODING

DAYTYPE_ORDER = ["평일", "토요일", "일요일·공휴일"]

PERIODS = {
    "dow": ["dow_cd", "dow_nm"],
    "daytype": ["day_type"],
    "month": ["month"],
}
ROUTE_LEVELS = {
    "route": ["base_no", "rte_id", "rte_no", "sttn_seq", "sttn_id", "sttn_nm"],
    "base": ["base_no", "sttn_id", "sttn_nm"],
}


class AggregateError(Exception):
    pass


def day_type(dow_cd: pd.Series, is_holiday: pd.Series) -> pd.Series:
    """일요일·공휴일 > 토요일 > 평일 순으로 판정 (토요일이 공휴일이면 일요일·공휴일)."""
    out = pd.Series("평일", index=dow_cd.index)
    out[dow_cd == "7"] = "토요일"
    out[(dow_cd == "1") | (is_holiday == "Y")] = "일요일·공휴일"
    return out


def load_clean(data_dir: Path, sgg_cd: str, year: int) -> pd.DataFrame:
    files = sorted(clean_dir(data_dir, sgg_cd).glob(f"{year}[0-9][0-9][0-9][0-9].csv"))
    if not files:
        raise AggregateError(f"{year}년 정제 파일이 없습니다 — 먼저 `python -m bus_congestion clean --year {year}` 실행")
    str_cols = {c: str for c in CLEAN_COLUMNS if c not in ("opr_trntm", "sttn_seq", "cgst", "est_pax")}
    df = pd.concat([pd.read_csv(f, dtype=str_cols, encoding=ENCODING, keep_default_na=False) for f in files],
                   ignore_index=True)
    df["day_type"] = day_type(df["dow_cd"], df["is_holiday"])
    df["month"] = df["opr_ymd"].str[4:6]
    return df


def summarize(df: pd.DataFrame, keys: list[str], threshold: int | None) -> pd.DataFrame:
    g = df.groupby(keys, sort=True, dropna=False)
    out = g.agg(
        n=("cgst", "size"),
        n_days=("opr_ymd", "nunique"),
        cgst_mean=("cgst", "mean"),
        cgst_max=("cgst", "max"),
        cgst_p50=("cgst", "median"),
        cgst_p90=("cgst", lambda s: s.quantile(0.9)),
    ).reset_index()
    if threshold is not None:
        ratio = df.assign(_c=df["cgst"] >= threshold).groupby(keys, sort=True, dropna=False)["_c"].mean()
        out["congested_ratio"] = ratio.round(4).to_numpy()
    for c in ("cgst_mean", "cgst_p50", "cgst_p90"):
        out[c] = out[c].round(2)
    return out


def aggregate_year(settings: Settings, data_dir: Path, year: int) -> dict[str, int]:
    """6개 집계표를 만들고 {파일명: 행 수}를 돌려준다."""
    sgg = settings["region"]["sgg_cd"]
    df = load_clean(data_dir, sgg, year)
    threshold = settings.congested_threshold
    out_dir = data_dir / "agg" / sgg / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for pname, pkeys in PERIODS.items():
        for lname, lkeys in ROUTE_LEVELS.items():
            table = summarize(df, pkeys + lkeys + ["tzon"], threshold)
            if pname == "daytype":
                table["day_type"] = pd.Categorical(table["day_type"], DAYTYPE_ORDER, ordered=True)
                table = table.sort_values(pkeys + lkeys + ["tzon"])
            name = f"by_{pname}_{lname}.csv"
            tmp = out_dir / f"{name}.tmp"
            table.to_csv(tmp, index=False, encoding=ENCODING)
            tmp.replace(out_dir / name)
            written[name] = len(table)
    return written
