"""매칭·정제·집계 테스트 (가짜 데이터, API 호출 없음)."""

import pandas as pd
import pytest

from bus_congestion.aggregate import day_type, summarize
from bus_congestion.clean import CLEAN_COLUMNS, CleanContext, clean_frame
from bus_congestion.matching import base_no, match_routes, normalize_query
from bus_congestion.storage import CONGESTION_COLUMNS


# ---- matching ----------------------------------------------------------
@pytest.mark.parametrize("rte_no, expected", [
    ("34연세대", "34"), ("34-1", "34-1"), ("34-1회촌종료", "34-1"), ("100-1기업도시", "100-1"),
    ("13성문사-원여고", "13"), ("111(연세대경유)", "111"), ("심야2", "심야2"), ("통학03", "통학03"),
    ("둘레길", "둘레길"), ("횡성순환-남산", "횡성순환-남산"), ("5(첫차)", "5"),
])
def test_base_no(rte_no, expected):
    assert base_no(rte_no) == expected


def test_match_routes():
    names = {"1": "34연세대", "2": "34매지리", "3": "34-1", "4": "340", "5": "30연세대"}
    assert match_routes("34", names) == ["1", "2"]
    assert match_routes(" 34-1 번", names) == ["3"]
    assert normalize_query("34 번") == "34"
    with pytest.raises(LookupError, match="비슷한 번호"):
        match_routes("35", names)


# ---- clean -------------------------------------------------------------
def raw_row(**over):
    base = dict(opr_ymd="20250505", dow_cd="2", dow_nm="월요일", ctpv_cd="51", ctpv_nm="강원특별자치도",
                sgg_cd="51130", sgg_nm="원주시", emd_cd="5113025000", emd_nm="흥업면", rte_id="43701901",
                opr_trntm="0", sttn_seq="5", sttn_id="4376591", trfc_mns_se_cd="B", tzon="08", cgst="44")
    return {**base, **over}


@pytest.fixture
def ctx():
    return CleanContext(
        campus_ids={"43701901", "43705802", "43709999"},
        route_names={"43701901": "34연세대", "43705802": "111(연세대경유)", "43709999": "조조", "43700100": "10"},
        stop_names={"4376591": "연세대학교"},
        holidays={"20250505"},
        excluded_route_nos=frozenset({"고장", "공차", "조조"}),
        tzon_min=0, tzon_max=23, pax_capacity=45,
    )


def test_clean_frame(ctx):
    rows = [
        raw_row(),
        raw_row(),                          # 완전 중복 → 제거
        raw_row(cgst="13"),                 # cgst만 다름 → 유지
        raw_row(rte_id="43700100"),         # 캠퍼스 노선 아님 → 제거
        raw_row(rte_id="43709999"),         # 제외 노선(조조) → 제거
        raw_row(tzon="24"),                 # 시간대 오류 → 제거
        raw_row(rte_id="43705802", sttn_id="9999999"),  # 수동 포함 노선, 정류장명 없음
    ]
    df, st = clean_frame(pd.DataFrame(rows, columns=CONGESTION_COLUMNS), ctx, "20250505")
    assert (st.raw_rows, st.duplicates, st.non_campus, st.excluded, st.bad_tzon, st.clean_rows) == (7, 1, 1, 1, 1, 3)
    assert list(df.columns) == CLEAN_COLUMNS
    first = df.iloc[0]
    assert (first.rte_no, first.base_no, first.sttn_nm, first.is_holiday) == ("34연세대", "34", "연세대학교", "Y")
    assert first.est_pax == 20                # 44% × 45명 = 19.8 → 20
    assert sorted(df["cgst"]) == [13, 44, 44]
    assert df.iloc[-1].sttn_nm == "" and df.iloc[-1].base_no == "111"
    assert df["tzon"].tolist()[0] == "08"     # 문자열 유지


def test_est_pax_round_half_up(ctx):
    # cgst 값은 round(인원/45×100)이므로 역산하면 원래 인원이 나와야 한다
    for pax in range(0, 46):
        cgst = int(pax * 100 / 45 + 0.5)
        df, _ = clean_frame(pd.DataFrame([raw_row(cgst=str(cgst))], columns=CONGESTION_COLUMNS), ctx)
        assert df.iloc[0].est_pax == pax


# ---- aggregate ---------------------------------------------------------
def test_day_type():
    dow = pd.Series(["2", "7", "1", "4", "7"])
    hol = pd.Series(["N", "N", "N", "Y", "Y"])
    assert day_type(dow, hol).tolist() == ["평일", "토요일", "일요일·공휴일", "일요일·공휴일", "일요일·공휴일"]


def test_summarize():
    df = pd.DataFrame({"k": ["a"] * 4 + ["b"], "opr_ymd": ["1", "1", "2", "2", "1"], "cgst": [10, 20, 30, 40, 5]})
    out = summarize(df, ["k"], threshold=25).set_index("k")
    assert out.loc["a", "n"] == 4 and out.loc["a", "n_days"] == 2
    assert out.loc["a", "cgst_mean"] == 25 and out.loc["a", "cgst_max"] == 40 and out.loc["a", "cgst_p50"] == 25
    assert out.loc["a", "congested_ratio"] == 0.5 and out.loc["b", "congested_ratio"] == 0
    assert "congested_ratio" not in summarize(df, ["k"], threshold=None).columns
