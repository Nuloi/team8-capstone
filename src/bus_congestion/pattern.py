"""1단계 혼잡 패턴 지수: data/clean → data/result/{sgg}/{year}/

설계: docs/algorithm_design.md 2장
- 칸 = 노선 × 정류장 × 기간(학사일정) × 일자 유형 × 시간대
- 칸 안에서 날짜마다 평균 cgst 하나(날별 값)를 만들고, 모든 지표를 날별 값으로 계산한다.
- 수준 L = 보정 평균 (관측일이 적은 칸은 같은 노선·기간·일자유형·시간대 평균 쪽으로 당김)
- 등급(임시) = L 분포의 분위수로 3단계 T1 여유 / T2 보통 / T3 혼잡
- 빈도 F, 시간 지속 D_t, 구간 지속 D_s 는 T3 경계(T_혼잡) 기준
결과 파일은 웹 파트에 넘기는 산출물이다(형식은 웹 팀원과 합의 전 — 초안).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bus_congestion.aggregate import DAYTYPE_ORDER, load_clean
from bus_congestion.config import Settings, load_academic_calendar
from bus_congestion.storage import ENCODING

PERIOD_ORDER = ["학기", "시험", "계절학기", "방학"]
GRADES = [("T1", "여유"), ("T2", "보통"), ("T3", "혼잡")]
CONTEXT = ["period", "day_type", "tzon"]

# 노선 단위별 칸 키와 보정(prior)에 쓰는 노선 키
LEVELS = {
    "base": {"keys": ["base_no", "sttn_id"], "prior": ["base_no"]},
    "route": {"keys": ["base_no", "rte_id", "sttn_seq", "sttn_id"], "prior": ["rte_id"]},
}


class PatternError(Exception):
    pass


@dataclass(frozen=True)
class Thresholds:
    t_low: float   # T1|T2 경계
    t_high: float  # T2|T3 경계 = T_혼잡
    q_low: float
    q_high: float


# ---- 입력 ----------------------------------------------------------------
def load_frame(settings: Settings, data_dir: Path, year: int, calendar: dict[str, str] | None = None) -> pd.DataFrame:
    df = load_clean(data_dir, settings["region"]["sgg_cd"], year)
    calendar = calendar if calendar is not None else load_academic_calendar()
    df["period"] = df["opr_ymd"].map(calendar)
    missing = sorted(df.loc[df["period"].isna(), "opr_ymd"].unique())
    if missing:
        raise PatternError(f"학사일정(config/academic_calendar.csv)에 없는 날짜: {missing[:5]} 외 {max(0, len(missing) - 5)}개")
    return df


def daily_values(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """칸 × 날짜 → 날별 값 x(그날 평균 cgst)와 관측 수."""
    return (df.groupby(keys + CONTEXT + ["opr_ymd"], sort=False)
              .agg(x=("cgst", "mean"), n_obs=("cgst", "size"))
              .reset_index())


# ---- 지표 ----------------------------------------------------------------
def cell_levels(daily: pd.DataFrame, keys: list[str], prior_keys: list[str], k: float) -> pd.DataFrame:
    """칸별 n_days, n_obs, 원 평균, p90, 보정 수준 L."""
    cells = (daily.groupby(keys + CONTEXT, sort=False)
                  .agg(n_days=("opr_ymd", "nunique"), n_obs=("n_obs", "sum"),
                       cgst_mean_raw=("x", "mean"), cgst_p90=("x", lambda s: s.quantile(0.9)))
                  .reset_index())
    prior = daily.groupby(prior_keys + CONTEXT, sort=False)["x"].mean().rename("prior").reset_index()
    cells = cells.merge(prior, on=prior_keys + CONTEXT, how="left")
    cells["level"] = (cells["n_days"] * cells["cgst_mean_raw"] + k * cells["prior"]) / (cells["n_days"] + k)
    return cells.drop(columns="prior")


def compute_thresholds(levels: pd.Series, quantiles: list[float]) -> Thresholds:
    if len(levels) == 0:
        raise PatternError("등급 경계를 계산할 칸이 없습니다.")
    q_low, q_high = quantiles
    return Thresholds(float(levels.quantile(q_low)), float(levels.quantile(q_high)), q_low, q_high)


def assign_grade(level: pd.Series, th: Thresholds) -> pd.Series:
    codes = np.select([level < th.t_low, level < th.t_high], ["T1", "T2"], default="T3")
    return pd.Series(codes, index=level.index)


def run_lengths(frame: pd.DataFrame, group: list[str], order: str, flag: str, step: int | None) -> pd.Series:
    """flag가 True인 행이 group 안에서 order 순으로 연속된 길이(그 행이 속한 연속 구간의 길이).

    step=1이면 order 값이 1씩 이어질 때만 연속(시간대), None이면 관측된 순서상 이웃이면 연속(정류장).
    flag가 False인 행은 NaN.
    """
    d = frame.sort_values(group + [order])
    gid = d.groupby(group, sort=False).ngroup()
    flagged = d[flag].to_numpy()
    adjacent = (gid == gid.shift()).to_numpy() & np.roll(flagged, 1)
    adjacent[0] = False
    if step is not None:
        adjacent &= (d[order] - d[order].shift() == step).to_numpy()
    run_id = np.cumsum(flagged & ~adjacent)
    run = pd.Series(np.where(flagged, run_id, -1), index=d.index)
    length = run[flagged].map(run[flagged].value_counts())
    return length.reindex(frame.index)


def add_frequency_duration(cells: pd.DataFrame, daily: pd.DataFrame, keys: list[str],
                           t_high: float, with_segment: bool) -> pd.DataFrame:
    daily = daily.copy()
    daily["congested"] = daily["x"] >= t_high
    daily["_t"] = daily["tzon"].astype(int)
    # 시간 지속: 같은 날·정류장에서 혼잡이 몇 시간 연속되는가
    daily["run_hours"] = run_lengths(daily, keys + ["period", "day_type", "opr_ymd"], "_t", "congested", step=1)
    agg = {"congested_days_ratio": ("congested", "mean"), "duration_hours": ("run_hours", "mean")}
    if with_segment:
        # 구간 지속: 같은 날·시간대에 노선 순서상 이웃한 정류장이 몇 개 연속 혼잡한가
        daily["run_stops"] = run_lengths(daily, ["rte_id", "period", "day_type", "opr_ymd", "tzon"],
                                         "sttn_seq", "congested", step=None)
        agg["segment_stops"] = ("run_stops", "mean")
    extra = daily.groupby(keys + CONTEXT, sort=False).agg(**agg).reset_index()
    return cells.merge(extra, on=keys + CONTEXT, how="left")


# ---- 결과물 --------------------------------------------------------------
def congestion_windows(base: pd.DataFrame) -> pd.DataFrame:
    """T3 혼잡 칸이 연속된 시간대 구간과 피크 (기본번호 단위)."""
    group = ["base_no", "sttn_id", "sttn_nm", "period", "day_type"]
    t3 = base[base["grade"] == "T3"].copy()
    if t3.empty:
        return pd.DataFrame(columns=group + ["start_tzon", "end_tzon", "hours", "peak_tzon", "peak_level"])
    t3["_t"] = t3["tzon"].astype(int)
    t3 = t3.sort_values(group + ["_t"])
    gid = t3.groupby(group, sort=False).ngroup()
    new_run = (gid != gid.shift()) | (t3["_t"] - t3["_t"].shift() != 1)
    t3["_run"] = new_run.cumsum()
    peak = t3.loc[t3.groupby("_run")["level"].idxmax(), ["_run", "tzon", "level"]]
    win = (t3.groupby(group + ["_run"], sort=False)
             .agg(start_tzon=("tzon", "first"), end_tzon=("tzon", "last"), hours=("tzon", "size"))
             .reset_index()
             .merge(peak.rename(columns={"tzon": "peak_tzon", "level": "peak_level"}), on="_run"))
    return win.drop(columns="_run")


def recommendations(base: pd.DataFrame, window: int) -> pd.DataFrame:
    """T3 혼잡 칸마다 같은 정류장·노선·기간·일자유형의 ±window시간 중 등급이 더 낮고 L이 가장 낮은 시간."""
    group = ["base_no", "sttn_id", "sttn_nm", "period", "day_type"]
    cols = group + ["tzon", "level", "grade"]
    src = base.loc[base["grade"] == "T3", cols]
    cand = base.loc[base["grade"] != "T3", cols]
    m = src.merge(cand, on=group, suffixes=("", "_rec"))
    m["shift_hours"] = m["tzon_rec"].astype(int) - m["tzon"].astype(int)
    m["_abs"] = m["shift_hours"].abs()
    m = m[m["_abs"] <= window]
    # L이 가장 낮은 시간, 같으면 원래 시간에 가까운 쪽
    m = m.sort_values(group + ["tzon", "level_rec", "_abs"]).drop_duplicates(group + ["tzon"])
    return m.rename(columns={"tzon": "from_tzon", "level": "from_level", "grade": "from_grade",
                             "tzon_rec": "rec_tzon", "level_rec": "rec_level", "grade_rec": "rec_grade"}
                    ).drop(columns="_abs")


def finalize(cells: pd.DataFrame, capacity: int) -> pd.DataFrame:
    cells["est_pax"] = (cells["level"] * capacity / 100 + 0.5).astype(int)
    cells["grade_name"] = cells["grade"].map(dict(GRADES))
    for c in ("cgst_mean_raw", "cgst_p90", "level", "duration_hours", "segment_stops"):
        if c in cells:
            cells[c] = cells[c].round(2)
    if "congested_days_ratio" in cells:
        cells["congested_days_ratio"] = cells["congested_days_ratio"].round(4)
    return cells


def sort_cells(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    df = df.copy()
    df["period"] = pd.Categorical(df["period"], PERIOD_ORDER, ordered=True)
    df["day_type"] = pd.Categorical(df["day_type"], DAYTYPE_ORDER, ordered=True)
    return df.sort_values([k for k in keys if k in df] + [c for c in CONTEXT if c in df])


# ---- 실행 ----------------------------------------------------------------
def build_pattern(df: pd.DataFrame, settings: Settings) -> dict[str, pd.DataFrame]:
    p = settings["pattern"]
    if p["grade_method"] != "quantile":
        raise PatternError(f"지원하지 않는 grade_method: {p['grade_method']}")
    k, capacity = p["shrinkage_k"], settings["clean"]["pax_capacity"]
    stop_names = df.groupby("sttn_id")["sttn_nm"].agg(lambda s: s.iloc[-1])
    route_names = df.groupby("rte_id")["rte_no"].agg(lambda s: s.iloc[-1])

    daily = {lv: daily_values(df, spec["keys"]) for lv, spec in LEVELS.items()}
    cells = {lv: cell_levels(daily[lv], spec["keys"], spec["prior"], k) for lv, spec in LEVELS.items()}
    th = compute_thresholds(cells["base"]["level"], p["grade_quantiles"])

    out = {}
    for lv, spec in LEVELS.items():
        c = cells[lv]
        c["grade"] = assign_grade(c["level"], th)
        c = add_frequency_duration(c, daily[lv], spec["keys"], th.t_high, with_segment=(lv == "route"))
        c["sttn_nm"] = c["sttn_id"].map(stop_names)
        if lv == "route":
            c["rte_no"] = c["rte_id"].map(route_names)
        out[lv] = finalize(c, capacity)

    base_cols = ["base_no", "sttn_id", "sttn_nm", "period", "day_type", "tzon", "n_days", "n_obs",
                 "cgst_mean_raw", "cgst_p90", "level", "est_pax", "grade", "grade_name",
                 "congested_days_ratio", "duration_hours"]
    route_cols = ["base_no", "rte_id", "rte_no", "sttn_seq", "sttn_id", "sttn_nm"] + base_cols[3:] + ["segment_stops"]
    base = sort_cells(out["base"][base_cols], LEVELS["base"]["keys"])
    route = sort_cells(out["route"][route_cols], LEVELS["route"]["keys"])
    windows = congestion_windows(out["base"])
    windows["peak_level"] = windows["peak_level"].round(2)
    recs = recommendations(out["base"], p["recommend_window_hours"])
    thresholds = pd.DataFrame([{
        "method": p["grade_method"], "q_low": th.q_low, "q_high": th.q_high,
        "t_low": round(th.t_low, 2), "t_high": round(th.t_high, 2),
        "t1": f"level < {th.t_low:.2f}", "t2": f"{th.t_low:.2f} <= level < {th.t_high:.2f}",
        "t3": f"level >= {th.t_high:.2f}",
        "n_cells": len(base), "n_days": df["opr_ymd"].nunique(),
        "date_min": df["opr_ymd"].min(), "date_max": df["opr_ymd"].max(),
        "provisional": True, "generated_at": datetime.now().isoformat(timespec="seconds"),
    }])
    return {
        "pattern_base.csv": base,
        "pattern_route.csv": route,
        "congestion_windows.csv": sort_cells(windows, ["base_no", "sttn_id"]),
        "recommendations.csv": sort_cells(recs, ["base_no", "sttn_id"]),
        "grade_thresholds.csv": thresholds,
    }


def run_pattern(settings: Settings, data_dir: Path, year: int) -> dict[str, int]:
    df = load_frame(settings, data_dir, year)
    tables = build_pattern(df, settings)
    out_dir = data_dir / "result" / settings["region"]["sgg_cd"] / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        tmp = out_dir / f"{name}.tmp"
        table.to_csv(tmp, index=False, encoding=ENCODING)
        tmp.replace(out_dir / name)
    return {name: len(t) for name, t in tables.items()}
