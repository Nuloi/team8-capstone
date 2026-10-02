from bus_congestion.config import load_settings


def test_settings_load():
    s = load_settings()
    assert s["region"]["ctpv_cd"] == "51"
    assert s["region"]["sgg_cd"] == "51130"
    assert s["api"]["num_of_rows"] <= 1000


def test_lists_are_strings_with_expected_values():
    s = load_settings()
    assert len(s.campus_stop_ids) == 10
    assert "4376591" in s.campus_stop_ids  # 연세대학교
    assert s.campus_manual_route_ids == {"43705802"}  # 111(연세대경유)
    assert s.excluded_route_nos == {"고장", "공차", "조조"}


def test_congested_threshold_undecided():
    # 혼잡 기준값은 아직 미정 → None
    assert load_settings().congested_threshold is None
