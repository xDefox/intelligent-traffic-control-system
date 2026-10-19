from backend.core.lane_utils import (
    denormalize_lane_id,
    extract_approach_from_camera_id,
    extract_intersection_id_from_camera_id,
    normalize_lane_id,
    parse_lane_id,
)


def test_normalize_adds_prefix_when_missing():
    assert normalize_lane_id("intersection_1_approach_0") == "lane_intersection_1_approach_0"


def test_normalize_keeps_existing_prefix():
    assert normalize_lane_id("lane_intersection_1_approach_0") == "lane_intersection_1_approach_0"


def test_normalize_returns_empty_for_empty_input():
    assert normalize_lane_id("") == ""


def test_normalize_is_idempotent():
    once = normalize_lane_id("intersection_1_approach_0")
    assert normalize_lane_id(once) == once


def test_denormalize_strips_prefix():
    assert denormalize_lane_id("lane_intersection_1_approach_0") == "intersection_1_approach_0"


def test_denormalize_leaves_id_without_prefix():
    assert denormalize_lane_id("intersection_1_approach_0") == "intersection_1_approach_0"


def test_denormalize_round_trip_returns_original():
    assert denormalize_lane_id(normalize_lane_id("intersection_1_approach_0")) == "intersection_1_approach_0"


def test_parse_empty_lane_returns_unknown():
    assert parse_lane_id("") == ("unknown", "")


def test_parse_lane_id_splits_intersection_and_approach():
    assert parse_lane_id("lane_intersection_1_approach_0") == ("intersection_1", "approach_0")


def test_parse_lane_id_returns_unknown_for_bad_format():
    assert parse_lane_id("lane_intersection_1approach_0") == ("unknown", "intersection_1approach_0")


def test_parse_lane_id_keeps_second_approach_marker():
    assert parse_lane_id("lane_intersection_1_approach_0_approach_1") == (
        "intersection_1",
        "approach_0_approach_1",
    )


def test_extract_approach_from_empty_camera_id():
    assert extract_approach_from_camera_id("") == ""


def test_extract_approach_from_camera_id():
    assert extract_approach_from_camera_id("intersection_1_approach_0") == "approach_0"


def test_extract_approach_returns_input_for_unknown_format():
    assert extract_approach_from_camera_id("str_str_str") == "str_str_str"


def test_extract_intersection_from_camera_id():
    assert extract_intersection_id_from_camera_id("intersection_1_approach_0") == "intersection_1"


def test_extract_intersection_returns_input_for_unknown_format():
    # "appoarch" — не маркер подхода, формат не распознан: возвращаем как есть.
    assert extract_intersection_id_from_camera_id("intersection_1_appoarch_0") == "intersection_1_appoarch_0"


def test_extract_intersection_from_empty_camera_id():
    assert extract_intersection_id_from_camera_id("") == ""
