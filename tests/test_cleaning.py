"""Cleaning primitives: null-like values, parsing, state/PIN normalisation."""

import pandas as pd

from caregap_map.cleaning import (
    dedupe_facilities,
    normalize_null_like,
    normalize_pincode,
    normalize_state_verbose,
    parse_coordinates,
    parse_float_safe,
    parse_int_safe,
    parse_list_field,
)


class TestNullLike:
    def test_placeholders_become_none(self):
        for raw in ["null", "NULL", "None", "n/a", "NA", "", "  ", "[]", "{}", "-", "nan"]:
            assert normalize_null_like(raw) is None, raw

    def test_real_values_survive(self):
        assert normalize_null_like("  ICU ward ") == "ICU ward"
        assert normalize_null_like(0) == "0"  # numeric zero is a value, not a null

    def test_nan_is_none(self):
        assert normalize_null_like(float("nan")) is None


class TestListField:
    def test_json_array(self):
        assert parse_list_field('["a", "b"]') == ["a", "b"]

    def test_null_like_items_removed(self):
        assert parse_list_field('["a", "null", ""]') == ["a"]

    def test_empty_array_placeholder(self):
        assert parse_list_field("[]") == []
        assert parse_list_field(None) == []

    def test_malformed_json_kept_as_single_fragment(self):
        # Never discard original text just because it does not parse.
        raw = '["broken", "unterminated'
        assert parse_list_field(raw) == [raw]

    def test_scalar_string(self):
        assert parse_list_field("ICU available") == ["ICU available"]


class TestNumericParsing:
    def test_int_from_messy_text(self):
        assert parse_int_safe("650") == 650
        assert parse_int_safe("about 1,200 beds") == 1200
        assert parse_int_safe("null") is None

    def test_float_nfhs_forms(self):
        assert parse_float_safe("(29.5)") == 29.5
        assert parse_float_safe("*") is None
        assert parse_float_safe("1,234.5") == 1234.5
        assert parse_float_safe("no data") is None


class TestCoordinates:
    def test_valid_india(self):
        lat, lon, status = parse_coordinates("11.93", "79.48")
        assert status == "ok" and lat == 11.93 and lon == 79.48

    def test_missing(self):
        assert parse_coordinates(None, None)[2] == "missing"
        assert parse_coordinates("null", "")[2] == "missing"

    def test_unparseable(self):
        assert parse_coordinates("abc", "79.48")[2] == "unparseable"

    def test_out_of_range_kept_not_dropped(self):
        lat, lon, status = parse_coordinates("51.5", "-0.12")  # London
        assert status == "out_of_range"
        assert lat == 51.5  # value preserved for inspection

    def test_zero_zero_is_invalid(self):
        assert parse_coordinates("0", "0")[2] == "out_of_range"


class TestPincode:
    def test_plain(self):
        assert normalize_pincode("682001") == "682001"

    def test_with_spaces_and_text(self):
        assert normalize_pincode("PIN 682 001") == "682001"

    def test_rejects_garbage(self):
        assert normalize_pincode("12345") is None  # 5 digits
        assert normalize_pincode("012345") is None  # cannot start with 0
        assert normalize_pincode(None) is None


class TestStateNormalisation:
    def test_exact(self):
        assert normalize_state_verbose("Tamil Nadu") == ("Tamil Nadu", "exact")
        assert normalize_state_verbose("WEST BENGAL") == ("West Bengal", "exact")

    def test_aliases(self):
        assert normalize_state_verbose("Orissa")[0] == "Odisha"
        assert normalize_state_verbose("Mh")[0] == "Maharashtra"
        assert normalize_state_verbose("Nct Of Delhi")[0] == "Delhi"
        assert normalize_state_verbose("Pondicherry")[0] == "Puducherry"
        assert normalize_state_verbose("Jammu & Kashmir")[0] == "Jammu and Kashmir"

    def test_suffix_match_lower_confidence(self):
        state, method = normalize_state_verbose("Ghaziabad, Uttar Pradesh")
        assert state == "Uttar Pradesh" and method == "suffix"

    def test_city_alone_is_not_guessed(self):
        assert normalize_state_verbose("Mumbai") == (None, "none")
        assert normalize_state_verbose("Hyderabad") == (None, "none")


class TestDedup:
    def test_exact_id_duplicates_keep_most_complete(self):
        df = pd.DataFrame(
            [
                {"unique_id": "a", "name": "A", "capacity": None},
                {"unique_id": "a", "name": "A", "capacity": "100"},
                {"unique_id": "b", "name": "B", "capacity": None},
            ]
        )
        out, dropped = dedupe_facilities(df)
        assert dropped == 1
        assert len(out) == 2
        assert out[out["unique_id"] == "a"].iloc[0]["capacity"] == "100"

    def test_same_name_different_ids_not_merged(self):
        df = pd.DataFrame(
            [
                {"unique_id": "a", "name": "Same Hospital"},
                {"unique_id": "b", "name": "Same Hospital"},
            ]
        )
        out, dropped = dedupe_facilities(df)
        assert dropped == 0 and len(out) == 2
