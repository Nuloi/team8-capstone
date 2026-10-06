"""1단계 혼잡 패턴 지수 테스트 (가짜 데이터)."""

import copy

import pandas as pd
import pytest

from bus_congestion.config import ConfigError, Settings, load_academic_calendar, load_settings
from bus_congestion.pattern import (
    Thresholds,
    assign_grade,
    build_pattern,
    cell_levels,
    compute_thresholds,
    congestion_windows,
    recommendations,
    run_lengths,
)


def test_academic_calendar_covers_2025():
    cal = load_academic_calendar()
    assert cal["20250304"] == "학기" and cal["20250422"] == "시험" and cal["20250701"] == "계절학기"
    assert cal["20250801"] == "방학" and cal["20251210"] == "시험"  # 자율학습 주간은 시험에 포함
    assert sum(k.startswith("2025") for k in cal) == 365


def test_academic_calendar_overlap(tmp_path):
    (tmp_path / "academic_calendar.csv").write_text(
        "start,end,period,label\n2025-01-01,2025-01-10,방학,a\n2025-01-10,2025-01-20,학기,b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="20250110"):
        load_academic_calendar(tmp_path)


def test_cell_levels_shrinkage():
    # 정류장 A: 10일 관측 평균 30, 정류장 B: 1일 관측 90 → B는 노선 평균 쪽으로 크게 당겨진다
    rows = [dict(base_no="34", sttn_id="A", period="학기", day_type="평일", tzon="08", opr_ymd=f"d{i}", x=30.0, n_obs=1)
            for i in range(10)]
    rows.append(dict(base_no="34", sttn_id="B", period="학기", day_type="평일", tzon="08", opr_ymd="d0", x=90.0, n_obs=1))
    cells = cell_levels(pd.DataFrame(rows), ["base_no", "sttn_id"], ["base_no"], k=5).set_index("sttn_id")
    prior = (30 * 10 + 90) / 11
    assert cells.loc["B", "level"] == pytest.approx((1 * 90 + 5 * prior) / 6)
    assert cells.loc["A", "level"] == pytest.approx((10 * 30 + 5 * prior) / 15)
    assert cells.loc["B", "cgst_mean_raw"] == 90 and cells.loc["A", "n_days"] == 10


def test_thresholds_and_grades():
    level = pd.Series(range(1, 10), dtype=float)
    th = compute_thresholds(level, [1 / 3, 2 / 3])
    grades = assign_grade(level, th)
    assert grades.value_counts().to_dict() == {"T1": 3, "T2": 3, "T3": 3}


def test_run_lengths_time_and_segment():
    f = pd.DataFrame({"g": ["a"] * 6 + ["b"] * 2,
                      "t": [7, 8, 9, 11, 12, 13, 8, 9],
                      "c": [True, True, False, True, True, True, True, True]})
    # 시간대: 1씩 이어질 때만 연속. a: 7-8 (2), 9 아님, 11-13 (3) / b: 8-9 (2)
    assert run_lengths(f, ["g"], "t", "c", step=1).fillna(-1).tolist() == [2, 2, -1, 3, 3, 3, 2, 2]
    # 정류장: 관측된 순서상 이웃이면 연속 → a: 7-8 (2), 11-13 (3) — 9와 11 사이는 9가 False라 끊김
    seg = run_lengths(f, ["g"], "t", "c", step=None).fillna(-1).tolist()
    assert seg == [2, 2, -1, 3, 3, 3, 2, 2]
    f.loc[2, "c"] = True  # 9도 혼잡 → 정류장 기준으로는 7~13 전부 연속(6), 시간 기준으로는 9-11 사이 끊김
    assert run_lengths(f, ["g"], "t", "c", step=None).tolist()[:6] == [6] * 6
    assert run_lengths(f, ["g"], "t", "c", step=1).tolist()[:6] == [3, 3, 3, 3, 3, 3]


def base_cells(levels_by_tzon, grade_by_tzon):
    return pd.DataFrame([dict(base_no="34", sttn_id="S", sttn_nm="원주의료원", period="학기", day_type="평일",
                              tzon=f"{t:02d}", level=float(lv), grade=grade_by_tzon[t])
                         for t, lv in levels_by_tzon.items()])


def test_windows_and_recommendations():
    levels = {8: 10, 9: 40, 10: 50, 11: 45, 12: 20, 13: 42, 16: 15}
    grades = {8: "T1", 9: "T3", 10: "T3", 11: "T3", 12: "T2", 13: "T3", 16: "T1"}
    base = base_cells(levels, grades)
    win = congestion_windows(base)
    assert win[["start_tzon", "end_tzon", "hours", "peak_tzon"]].values.tolist() == \
        [["09", "11", 3, "10"], ["13", "13", 1, "13"]]
    rec = recommendations(base, window=2).set_index("from_tzon")
    assert rec.loc["09", "rec_tzon"] == "08"           # ±2 중 L 최저(8시 10)
    assert rec.loc["11", "rec_tzon"] == "12"           # 9·10·13은 T3라 후보 아님 → 12시
    assert rec.loc["13", "rec_tzon"] == "12"           # 16시는 3시간 떨어져 제외
    assert rec.loc["11", "shift_hours"] == 1


@pytest.fixture
def settings():
    s = load_settings()
    raw = copy.deepcopy(s.raw)
    return Settings(raw, s.campus_stop_ids, s.campus_manual_route_ids, s.excluded_route_nos)


def test_build_pattern_end_to_end(settings):
    rows = []
    for day in ["20250304", "20250305", "20250306"]:
        for t, c in [("08", 60), ("09", 70), ("12", 5), ("13", 10)]:
            for seq, sid in [(1, "A"), (2, "B")]:
                rows.append(dict(opr_ymd=day, dow_cd="3", rte_id="43701901", rte_no="34연세대", base_no="34",
                                 sttn_seq=seq, sttn_id=sid, sttn_nm=f"정류장{sid}", tzon=t, cgst=c,
                                 period="학기", day_type="평일"))
    out = build_pattern(pd.DataFrame(rows), settings)
    base = out["pattern_base.csv"]
    assert set(base["grade"]) <= {"T1", "T2", "T3"}
    peak = base[base["tzon"] == "09"].iloc[0]
    assert peak["grade"] == "T3" and peak["congested_days_ratio"] == 1 and peak["duration_hours"] == 2
    route = out["pattern_route.csv"]
    assert route.loc[route["tzon"] == "09", "segment_stops"].tolist() == [2, 2]  # A·B 연속 혼잡
    assert out["congestion_windows.csv"][["start_tzon", "end_tzon"]].values.tolist()[0] == ["08", "09"]
    th = out["grade_thresholds.csv"].iloc[0]
    assert th["provisional"] and th["n_days"] == 3
