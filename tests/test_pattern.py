"""1단계 혼잡 패턴 지수 테스트 (가짜 데이터)."""

import copy

import pandas as pd
import pytest

from bus_congestion.config import ConfigError, Settings, load_academic_calendar, load_settings
from bus_congestion.matching import base_stop_directions, stop_directions, stop_label, stop_labels
from bus_congestion.pattern import (
    assign_grade,
    build_pattern,
    cell_levels,
    compute_thresholds,
    recommendations,
    run_lengths,
    time_windows,
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


# ---- 방향 ----------------------------------------------------------------
def test_stop_directions_round_trip():
    # 기점 A → B → C → 회차 T → C' → B' → 기점 A'  (같은 이름이 갈 때·올 때 한 번씩)
    stops = [("a1", "A", 0), ("b1", "B", 1), ("c1", "C", 2), ("t", "T", 3), ("c2", "C", 4), ("b2", "B", 5), ("a2", "A", 6)]
    df = pd.DataFrame([dict(rte_id="R", sttn_id=s, sttn_nm=n, sttn_seq=q) for s, n, q in stops])
    d = stop_directions(df).set_index("sttn_id")["direction"]
    assert d[["a1", "b1", "c1", "t"]].tolist() == ["상행"] * 4   # 회차 지점(3)까지 상행
    assert d[["c2", "b2", "a2"]].tolist() == ["하행"] * 3


def test_stop_directions_no_pairs_uses_midpoint():
    df = pd.DataFrame([dict(rte_id="R", sttn_id=f"s{i}", sttn_nm=f"n{i}", sttn_seq=i) for i in range(5)])
    assert stop_directions(df).sort_values("sttn_id")["direction"].tolist() == ["상행", "상행", "상행", "하행", "하행"]


def test_base_direction_majority_and_label():
    rd = pd.DataFrame({"rte_id": ["R1", "R2", "R1"], "sttn_id": ["x", "x", "y"],
                       "n_obs": [3, 10, 5], "direction": ["상행", "하행", "하행"]})
    assert base_stop_directions(rd).to_dict() == {"x": "하행", "y": "하행"}
    assert stop_label("원주의료원", "4374311", "상행") == "원주의료원(상행)"
    assert stop_label("", "4374311", "하행") == "4374311(하행)"


def test_stop_labels_disambiguate_same_direction():
    stops = pd.DataFrame({"sttn_id": ["4370241", "4370861", "4381111", "4382222"],
                          "sttn_nm": ["원주의료원", "원주의료원", "구억동", "구억동"],
                          "direction": ["상행", "하행", "상행", "상행"]})
    assert stop_labels(stops).to_dict() == {"4370241": "원주의료원(상행)", "4370861": "원주의료원(하행)",
                                            "4381111": "구억동(상행·1111)", "4382222": "구억동(상행·2222)"}


# ---- 수준·등급 -----------------------------------------------------------
def test_cell_levels_shrinkage():
    rows = [dict(base_no="34", sttn_id="A", period="학기", day_type="평일", tzon="08", opr_ymd=f"d{i}", x=30.0, n_obs=1)
            for i in range(10)]
    rows.append(dict(base_no="34", sttn_id="B", period="학기", day_type="평일", tzon="08", opr_ymd="d0", x=90.0, n_obs=1))
    cells = cell_levels(pd.DataFrame(rows), ["base_no", "sttn_id"], ["base_no"], k=5).set_index("sttn_id")
    prior = (30 * 10 + 90) / 11
    assert cells.loc["B", "level"] == pytest.approx((1 * 90 + 5 * prior) / 6)
    assert cells.loc["A", "level"] == pytest.approx((10 * 30 + 5 * prior) / 15)


def test_thresholds_and_grades():
    level = pd.Series(range(1, 10), dtype=float)
    grades = assign_grade(level, compute_thresholds(level, [1 / 3, 2 / 3]))
    assert grades.value_counts().to_dict() == {"T1": 3, "T2": 3, "T3": 3}


def test_run_lengths_time_and_segment():
    f = pd.DataFrame({"g": ["a"] * 6 + ["b"] * 2,
                      "t": [7, 8, 9, 11, 12, 13, 8, 9],
                      "c": [True, True, False, True, True, True, True, True]})
    assert run_lengths(f, ["g"], "t", "c", step=1).fillna(-1).tolist() == [2, 2, -1, 3, 3, 3, 2, 2]
    assert run_lengths(f, ["g"], "t", "c", step=None).fillna(-1).tolist() == [2, 2, -1, 3, 3, 3, 2, 2]
    f.loc[2, "c"] = True  # 정류장 기준은 7~13 전부 연속(6), 시간 기준은 9와 11 사이에서 끊김
    assert run_lengths(f, ["g"], "t", "c", step=None).tolist()[:6] == [6] * 6
    assert run_lengths(f, ["g"], "t", "c", step=1).tolist()[:6] == [3] * 6


# ---- 시간대 구간·추천 -----------------------------------------------------
GROUP = ["base_no", "direction", "period", "day_type"]


def bus_cells(levels_grades):
    return pd.DataFrame([dict(base_no="34", direction="상행", period="학기", day_type="평일",
                              tzon=f"{t:02d}", level=float(lv), grade=g) for t, (lv, g) in levels_grades.items()])


CELLS = {6: (3, "T1"), 7: (5, "T1"), 8: (12, "T2"), 9: (40, "T3"), 10: (50, "T3"), 11: (45, "T3"),
         12: (11, "T2"), 13: (42, "T3"), 14: (35, "T3"), 15: (38, "T3"), 16: (37, "T3"), 20: (2, "T1"), 21: (4, "T1")}


def test_time_windows_congested_and_quiet():
    c = bus_cells(CELLS)
    hot = time_windows(c, GROUP, "T3")
    assert hot[["start_tzon", "end_tzon", "hours", "peak_tzon"]].values.tolist() == \
        [["09", "11", 3, "10"], ["13", "16", 4, "13"]]
    quiet = time_windows(c, GROUP, "T1")
    assert quiet[["start_tzon", "end_tzon", "hours", "calmest_tzon"]].values.tolist() == \
        [["06", "07", 2, "06"], ["20", "21", 2, "20"]]
    assert quiet["peak_tzon"].isna().all()


def test_recommendations_prefer_nearest_quiet():
    rec = recommendations(bus_cells(CELLS), GROUP).set_index("from_tzon")
    assert rec.loc["09", ["rec_tzon", "rec_grade"]].tolist() == ["07", "T1"]   # 8시(T2)보다 여유(T1) 우선
    assert rec.loc["16", ["rec_tzon", "rec_grade"]].tolist() == ["20", "T1"]   # ±2시간 밖이어도 여유를 찾음
    assert rec.loc["13", "rec_tzon"] == "07"   # 7시(6시간 전)와 20시(7시간 뒤) 중 더 가까운 쪽
    assert len(rec) == 7  # T3 칸 모두 추천을 받는다


def test_recommendations_fallback_to_normal_and_max_shift():
    c = bus_cells({8: (12, "T2"), 9: (40, "T3"), 10: (50, "T3")})
    rec = recommendations(c, GROUP).set_index("from_tzon")
    assert rec.loc["10", ["rec_tzon", "rec_grade", "shift_hours"]].tolist() == ["08", "T2", -2]
    assert "10" not in recommendations(c, GROUP, max_shift=1).set_index("from_tzon").index


# ---- 전체 실행 ------------------------------------------------------------
@pytest.fixture
def settings():
    s = load_settings()
    return Settings(copy.deepcopy(s.raw), s.campus_stop_ids, s.campus_manual_route_ids, s.excluded_route_nos)


def test_build_pattern_end_to_end(settings):
    # 왕복 노선: A(상행) → B(회차) → A'(하행). 상행 아침이 붐빔
    stops = [(0, "A1", "정류장A"), (1, "B", "회차점"), (2, "A2", "정류장A")]
    rows = []
    for day in ["20250304", "20250305", "20250306"]:
        for t, up, down in [("07", 10, 5), ("08", 70, 8), ("09", 65, 6), ("12", 6, 4), ("13", 12, 9)]:
            for seq, sid, nm in stops:
                rows.append(dict(opr_ymd=day, dow_cd="3", rte_id="43701901", rte_no="34연세대", base_no="34",
                                 sttn_seq=seq, sttn_id=sid, sttn_nm=nm, tzon=t, cgst=up if seq <= 1 else down,
                                 period="학기", day_type="평일"))
    out = build_pattern(pd.DataFrame(rows), settings)

    base = out["pattern_base.csv"]
    assert set(base.loc[base["sttn_nm"] == "정류장A", "sttn_label"]) == {"정류장A(상행)", "정류장A(하행)"}

    bus = out["bus_hourly.csv"]
    assert set(bus["direction"]) == {"상행", "하행"}
    up8 = bus[(bus["direction"] == "상행") & (bus["tzon"] == "08")].iloc[0]
    assert up8["grade"] == "T3" and up8["peak_sttn_label"] in {"정류장A(상행)", "회차점(상행)"}

    win = out["time_windows.csv"]
    assert set(win["scope"]) == {"버스", "정류장"} and set(win["kind"]) == {"혼잡", "여유"}
    hot = win[(win["scope"] == "버스") & (win["kind"] == "혼잡") & (win["direction"] == "상행")]
    assert ["08", "09"] in hot[["start_tzon", "end_tzon"]].values.tolist()  # 아침 피크 구간

    rec = out["recommendations.csv"]
    assert (rec["rec_grade"] != "T3").all() and set(rec["scope"]) <= {"버스", "정류장"}

    th = out["grade_thresholds.csv"]
    assert th["scope"].tolist() == ["버스", "정류장"] and th["provisional"].all()
