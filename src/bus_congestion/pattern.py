"""1단계 혼잡 패턴 지수: data/clean → data/result/{sgg}/{year}/

설계: docs/algorithm_design.md 2장
목표: "그 시간대 버스가 얼마나 혼잡한가"와 "언제 시간대가 비는가"를 노선·방향·정류장별로 낸다.
- 칸 = 노선 × (방향 또는 정류장) × 기간(학사일정) × 일자 유형 × 시간대
- 칸 안에서 날짜마다 평균 cgst 하나(날별 값)를 만들고, 모든 지표를 날별 값으로 계산한다.
- 수준 L = 보정 평균 (관측일이 적은 칸은 같은 노선의 평균 쪽으로 당김)
- 등급(임시) = L 분포의 분위수로 3단계 T1 여유 / T2 보통 / T3 혼잡 — 버스 단위·정류장 단위 경계를 따로 계산
- 빈도 F, 시간 지속 D_t, 구간 지속 D_s 는 각 단위의 T3 경계(T_혼잡) 기준
결과 파일은 웹 파트에 넘기는 산출물이다(형식은 웹 팀원과 합의 전 — 초안).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bus_congestion.aggregate import DAYTYPE_ORDER, load_clean
from bus_congestion.config import Settings, load_academic_calendar
from bus_congestion.matching import base_stop_directions, stop_directions, stop_labels
from bus_congestion.storage import ENCODING

PERIOD_ORDER = ["학기", "시험", "계절학기", "방학"]
DIRECTION_ORDER = ["상행", "하행"]
GRADES = [("T1", "여유"), ("T2", "보통"), ("T3", "혼잡")]
GRADE_NAME = dict(GRADES)
CONTEXT = ["period", "day_type", "tzon"]

# 단위별 칸 키, 보정(prior) 키, 보정 맥락
#   bus   : 노선 전체(방향별) — 버스가 그 시간대에 얼마나 혼잡한가
#   base  : 기본번호 × 정류장 — 정류장별
#   route : 변형 × 정류장 순서 — 세부
LEVELS = {
    "bus": {"keys": ["base_no", "direction"], "prior": ["base_no", "direction"], "prior_ctx": ["period", "day_type"]},
    "base": {"keys": ["base_no", "sttn_id"], "prior": ["base_no"], "prior_ctx": CONTEXT},
    "route": {"keys": ["base_no", "rte_id", "sttn_seq", "sttn_id"], "prior": ["rte_id"], "prior_ctx": CONTEXT},
}
SCOPE_NAME = {"bus": "버스", "base": "정류장"}


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


def add_directions(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """행마다 노선 기준 방향(상행/하행)을 붙이고, 기본번호 단위 정류장 방향도 돌려준다."""
    route_dir = stop_directions(df)
    df = df.merge(route_dir[["rte_id", "sttn_id", "direction"]], on=["rte_id", "sttn_id"], how="left")
    return df, base_stop_directions(route_dir)


def daily_values(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """칸 × 날짜 → 날별 값 x(그날 평균 cgst)와 관측 수."""
    return (df.groupby(keys + CONTEXT + ["opr_ymd"], sort=False)
              .agg(x=("cgst", "mean"), n_obs=("cgst", "size"))
              .reset_index())


# ---- 지표 ----------------------------------------------------------------
def cell_levels(daily: pd.DataFrame, keys: list[str], prior_keys: list[str], k: float,
                prior_ctx: list[str] = CONTEXT) -> pd.DataFrame:
    """칸별 n_days, n_obs, 원 평균, p90, 보정 수준 L."""
    cells = (daily.groupby(keys + CONTEXT, sort=False)
                  .agg(n_days=("opr_ymd", "nunique"), n_obs=("n_obs", "sum"),
                       cgst_mean_raw=("x", "mean"), cgst_p90=("x", lambda s: s.quantile(0.9)))
                  .reset_index())
    prior = daily.groupby(prior_keys + prior_ctx, sort=False)["x"].mean().rename("prior").reset_index()
    cells = cells.merge(prior, on=prior_keys + prior_ctx, how="left")
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
    # 시간 지속: 같은 날 같은 칸에서 혼잡이 몇 시간 연속되는가
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
def time_windows(cells: pd.DataFrame, group: list[str], grade: str) -> pd.DataFrame:
    """같은 group 안에서 grade가 연속되는 시간대 구간.

    혼잡(T3) 구간은 가장 붐비는 시간(peak), 여유(T1) 구간은 가장 한산한 시간(calmest)을 함께 낸다.
    """
    cols = group + ["start_tzon", "end_tzon", "hours", "peak_tzon", "peak_level", "calmest_tzon", "calmest_level"]
    sel = cells[cells["grade"] == grade].copy()
    if sel.empty:
        return pd.DataFrame(columns=cols)
    sel["_t"] = sel["tzon"].astype(int)
    sel = sel.sort_values(group + ["_t"])
    gid = sel.groupby(group, sort=False, dropna=False).ngroup()
    sel["_run"] = ((gid != gid.shift()) | (sel["_t"] - sel["_t"].shift() != 1)).cumsum()
    win = (sel.groupby(group + ["_run"], sort=False, dropna=False)
              .agg(start_tzon=("tzon", "first"), end_tzon=("tzon", "last"), hours=("tzon", "size"))
              .reset_index())
    pick = "idxmax" if grade == "T3" else "idxmin"
    focus = sel.loc[getattr(sel.groupby("_run")["level"], pick)(), ["_run", "tzon", "level"]]
    prefix = "peak" if grade == "T3" else "calmest"
    win = win.merge(focus.rename(columns={"tzon": f"{prefix}_tzon", "level": f"{prefix}_level"}), on="_run")
    return win.drop(columns="_run").reindex(columns=cols)


def recommendations(cells: pd.DataFrame, group: list[str], max_shift: int = 0) -> pd.DataFrame:
    """T3 혼잡 칸마다 같은 group(같은 노선·방향/정류장·기간·일자유형)에서 덜 혼잡한 시간을 고른다.

    여유(T1) 중 가장 가까운 시간 → 없으면 보통(T2) 중 가장 가까운 시간. 거리가 같으면 L이 낮은 쪽.
    max_shift > 0이면 ±max_shift시간 안에서만 찾는다.
    """
    cols = group + ["tzon", "level", "grade"]
    src = cells.loc[cells["grade"] == "T3", cols]
    cand = cells.loc[cells["grade"] != "T3", cols]
    m = src.merge(cand, on=group, suffixes=("", "_rec"))
    m["shift_hours"] = m["tzon_rec"].astype(int) - m["tzon"].astype(int)
    m["_abs"] = m["shift_hours"].abs()
    if max_shift:
        m = m[m["_abs"] <= max_shift]
    m = m.sort_values(group + ["tzon", "grade_rec", "_abs", "level_rec"]).drop_duplicates(group + ["tzon"])
    m["rec_grade_name"] = m["grade_rec"].map(GRADE_NAME)
    return m.rename(columns={"tzon": "from_tzon", "level": "from_level", "grade": "from_grade",
                             "tzon_rec": "rec_tzon", "level_rec": "rec_level", "grade_rec": "rec_grade"}
                    ).drop(columns="_abs")


def finalize(cells: pd.DataFrame, capacity: int) -> pd.DataFrame:
    cells["est_pax"] = (cells["level"] * capacity / 100 + 0.5).astype(int)
    cells["grade_name"] = cells["grade"].map(GRADE_NAME)
    for c in ("cgst_mean_raw", "cgst_p90", "level", "duration_hours", "segment_stops"):
        if c in cells:
            cells[c] = cells[c].round(2)
    if "congested_days_ratio" in cells:
        cells["congested_days_ratio"] = cells["congested_days_ratio"].round(4)
    return cells


def sort_rows(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    df = df.copy()
    order = {"period": PERIOD_ORDER, "day_type": DAYTYPE_ORDER, "direction": DIRECTION_ORDER}
    for col, cats in order.items():
        if col in df:
            df[col] = pd.Categorical(df[col], cats, ordered=True)
    return df.sort_values([k for k in keys + CONTEXT if k in df])


def round_levels(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if c.endswith("_level"):
            df[c] = pd.to_numeric(df[c]).round(2)
    return df


# ---- 실행 ----------------------------------------------------------------
def build_pattern(df: pd.DataFrame, settings: Settings) -> dict[str, pd.DataFrame]:
    p = settings["pattern"]
    if p["grade_method"] != "quantile":
        raise PatternError(f"지원하지 않는 grade_method: {p['grade_method']}")
    k, capacity = p["shrinkage_k"], settings["clean"]["pax_capacity"]
    max_shift = p.get("recommend_max_shift_hours", 0)

    df, base_dir = add_directions(df)
    stop_names = df.groupby("sttn_id")["sttn_nm"].agg(lambda s: s.iloc[-1])
    route_names = df.groupby("rte_id")["rte_no"].agg(lambda s: s.iloc[-1])

    daily = {lv: daily_values(df, spec["keys"]) for lv, spec in LEVELS.items()}
    cells = {lv: cell_levels(daily[lv], spec["keys"], spec["prior"], k, spec["prior_ctx"])
             for lv, spec in LEVELS.items()}
    th = {"bus": compute_thresholds(cells["bus"]["level"], p["grade_quantiles"]),
          "base": compute_thresholds(cells["base"]["level"], p["grade_quantiles"])}
    th["route"] = th["base"]  # 변형 단위는 정류장 단위 경계를 그대로 쓴다

    out = {}
    for lv, spec in LEVELS.items():
        c = cells[lv]
        c["grade"] = assign_grade(c["level"], th[lv])
        c = add_frequency_duration(c, daily[lv], spec["keys"], th[lv].t_high, with_segment=(lv == "route"))
        if lv != "bus":
            c["sttn_nm"] = c["sttn_id"].map(stop_names)
            if lv == "base":
                c["direction"] = c["sttn_id"].map(base_dir)
            else:
                c = c.merge(df[["rte_id", "sttn_id", "direction"]].drop_duplicates(), on=["rte_id", "sttn_id"], how="left")
                c["rte_no"] = c["rte_id"].map(route_names)
        out[lv] = finalize(c, capacity)

    # 정류장 표시명: 기본번호 단위 방향 기준으로 한 번 정해 정류장·변형 결과에 같이 쓴다
    labels = stop_labels(out["base"][["sttn_id", "sttn_nm", "direction"]])
    for lv in ("base", "route"):
        out[lv]["sttn_label"] = out[lv]["sttn_id"].map(labels)

    # 버스 단위: 그 시간대 가장 붐비는 정류장(같은 방향)
    base = out["base"]
    peak = base.loc[base.groupby(["base_no", "direction"] + CONTEXT)["level"].idxmax(),
                    ["base_no", "direction"] + CONTEXT + ["sttn_label", "level"]]
    bus = out["bus"].merge(peak.rename(columns={"sttn_label": "peak_sttn_label", "level": "peak_sttn_level"}),
                           on=["base_no", "direction"] + CONTEXT, how="left")

    metric_cols = ["n_days", "n_obs", "cgst_mean_raw", "cgst_p90", "level", "est_pax", "grade", "grade_name",
                   "congested_days_ratio", "duration_hours"]
    bus_cols = ["base_no", "direction"] + CONTEXT + metric_cols + ["peak_sttn_label", "peak_sttn_level"]
    base_cols = ["base_no", "sttn_id", "sttn_nm", "direction", "sttn_label"] + CONTEXT + metric_cols
    route_cols = (["base_no", "rte_id", "rte_no", "sttn_seq", "sttn_id", "sttn_nm", "direction", "sttn_label"]
                  + CONTEXT + metric_cols + ["segment_stops"])

    # 시간대 구간: 버스·정류장 단위 × 혼잡·여유
    bus_group = ["base_no", "direction", "period", "day_type"]
    stop_group = ["base_no", "direction", "sttn_id", "sttn_label", "period", "day_type"]
    win_parts = []
    for scope, cells_df, group in (("bus", bus, bus_group), ("base", base, stop_group)):
        for grade, kind in (("T3", "혼잡"), ("T1", "여유")):
            w = time_windows(cells_df, group, grade)
            w.insert(0, "kind", kind)
            w.insert(0, "scope", SCOPE_NAME[scope])
            win_parts.append(w)
    win_cols = ["scope", "kind", "base_no", "direction", "sttn_id", "sttn_label", "period", "day_type",
                "start_tzon", "end_tzon", "hours", "peak_tzon", "peak_level", "calmest_tzon", "calmest_level"]
    windows = round_levels(pd.concat(win_parts, ignore_index=True).reindex(columns=win_cols))

    rec_parts = []
    for scope, cells_df, group in (("bus", bus, bus_group), ("base", base, stop_group)):
        r = recommendations(cells_df, group, max_shift)
        r.insert(0, "scope", SCOPE_NAME[scope])
        rec_parts.append(r)
    rec_cols = ["scope", "base_no", "direction", "sttn_id", "sttn_label", "period", "day_type",
                "from_tzon", "from_level", "from_grade", "rec_tzon", "rec_level", "rec_grade", "rec_grade_name",
                "shift_hours"]
    recs = round_levels(pd.concat(rec_parts, ignore_index=True).reindex(columns=rec_cols))

    now = datetime.now().isoformat(timespec="seconds")
    thresholds = pd.DataFrame([{
        "scope": SCOPE_NAME[lv], "method": p["grade_method"], "q_low": t.q_low, "q_high": t.q_high,
        "t_low": round(t.t_low, 2), "t_high": round(t.t_high, 2),
        "t1": f"level < {t.t_low:.2f}", "t2": f"{t.t_low:.2f} <= level < {t.t_high:.2f}", "t3": f"level >= {t.t_high:.2f}",
        "n_cells": len(out[lv]), "n_days": df["opr_ymd"].nunique(),
        "date_min": df["opr_ymd"].min(), "date_max": df["opr_ymd"].max(),
        "provisional": True, "generated_at": now,
    } for lv, t in (("bus", th["bus"]), ("base", th["base"]))])

    return {
        "bus_hourly.csv": sort_rows(bus[bus_cols], ["base_no", "direction"]),
        "pattern_base.csv": sort_rows(base[base_cols], ["base_no", "direction", "sttn_id"]),
        "pattern_route.csv": sort_rows(out["route"][route_cols], ["base_no", "rte_id", "sttn_seq", "sttn_id"]),
        "time_windows.csv": sort_rows(windows, ["scope", "kind", "base_no", "direction", "sttn_id"]),
        "recommendations.csv": sort_rows(recs, ["scope", "base_no", "direction", "sttn_id"]),
        "grade_thresholds.csv": thresholds,
    }


OBSOLETE_FILES = ["congestion_windows.csv"]  # time_windows.csv로 대체됨


def run_pattern(settings: Settings, data_dir: Path, year: int) -> dict[str, int]:
    df = load_frame(settings, data_dir, year)
    tables = build_pattern(df, settings)
    out_dir = data_dir / "result" / settings["region"]["sgg_cd"] / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        tmp = out_dir / f"{name}.tmp"
        table.to_csv(tmp, index=False, encoding=ENCODING)
        tmp.replace(out_dir / name)
    for name in OBSOLETE_FILES:
        (out_dir / name).unlink(missing_ok=True)
    return {name: len(t) for name, t in tables.items()}
