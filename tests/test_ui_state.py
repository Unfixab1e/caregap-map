"""Region selection in query parameters: parsing and URL-state helpers."""

from __future__ import annotations

from caregap_map.ui_state import desired_region_params, normalize_region_request


class TestNormalizeRegionRequest:
    def test_state_and_district(self):
        assert normalize_region_request("Kerala", "Ernakulam") == {
            "state": "Kerala",
            "district": "Ernakulam",
        }

    def test_state_only(self):
        assert normalize_region_request("Kerala", None) == {"state": "Kerala", "district": None}

    def test_no_params_is_none(self):
        assert normalize_region_request(None, None) is None
        assert normalize_region_request("", "") is None

    def test_whitespace_counts_as_absent(self):
        assert normalize_region_request("   ", "  ") is None

    def test_district_without_state_is_ignored(self):
        assert normalize_region_request(None, "Ernakulam") is None

    def test_unknown_values_pass_through_for_later_validation(self):
        # Membership validation happens against the loaded data; the parser
        # never rejects by value (fallback to All India is the widget's job).
        assert normalize_region_request("Atlantis", "Nowhere") == {
            "state": "Atlantis",
            "district": "Nowhere",
        }


class TestDesiredRegionParams:
    def test_all_india_means_no_params(self):
        assert desired_region_params("All India", None) == {}

    def test_state_only(self):
        assert desired_region_params("Kerala", None) == {"state": "Kerala"}

    def test_state_and_district(self):
        assert desired_region_params("Kerala", "Ernakulam") == {
            "state": "Kerala",
            "district": "Ernakulam",
        }

    def test_district_never_appears_without_state(self):
        assert desired_region_params("All India", "Ernakulam") == {}
