"""Priority-facility selection, distribution and demo examples (D24)."""

from __future__ import annotations

import json

import pandas as pd

from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_NEEDS_REVIEW,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
)
from caregap_map.ui_components import (
    example_regions,
    hero_counts_html,
    humanize_flag,
    primary_flag,
    select_priority_facilities,
    status_distribution,
)


def facility(uid, cls, evidence=0, completeness=80, flags=0, flag_names=None):
    return {
        "unique_id": uid,
        "name": f"Facility {uid}",
        "address_city": "City",
        "district_final": "District",
        "classification": cls,
        "capability_evidence_score": evidence,
        "data_completeness_score": completeness,
        "n_validation_flags": flags,
        "validation_flags_json": json.dumps(
            [{"name": n, "severity": "suspicious", "detail": "d"} for n in (flag_names or [])]
        ),
    }


class TestPrioritySelection:
    def test_tier_order_uses_only_existing_statuses(self):
        subset = pd.DataFrame(
            [
                facility("gap", CLASS_LIKELY_GAP),
                facility("trusted_clean", CLASS_TRUSTED, evidence=90),
                facility("review_low", CLASS_NEEDS_REVIEW, evidence=20),
                facility(
                    "trusted_flagged",
                    CLASS_TRUSTED,
                    evidence=80,
                    flags=1,
                    flag_names=["directory_or_partner_content_detected"],
                ),
                facility("review_high", CLASS_NEEDS_REVIEW, evidence=85),
                facility("insufficient", CLASS_INSUFFICIENT, completeness=20),
            ]
        )
        priority = select_priority_facilities(subset, REGION_NEEDS_REVIEW)
        ids = priority["unique_id"].tolist()
        # Reviews first (strongest claim first), then flagged trusted, then
        # clean trusted; gap records only for planning-gap regions.
        assert ids == ["review_high", "review_low", "trusted_flagged", "trusted_clean"]
        assert "gap" not in ids and "insufficient" not in ids
        assert set(priority["priority_reason"]) <= {
            "Unresolved ICU claim — human review required",
            "Trusted evidence carrying validator flags — spot-check first",
            "Trusted evidence — verify operational details",
        }

    def test_gap_records_included_only_for_planning_gap_regions(self):
        subset = pd.DataFrame(
            [facility("gap1", CLASS_LIKELY_GAP, completeness=95), facility("gap2", CLASS_LIKELY_GAP)]
        )
        assert select_priority_facilities(subset, REGION_TRUSTED).empty
        gap_priority = select_priority_facilities(subset, REGION_PLANNING_GAP)
        assert gap_priority["unique_id"].tolist() == ["gap1", "gap2"]
        assert (gap_priority["priority_reason"] == "Well-populated record without ICU evidence").all()

    def test_limit_and_determinism(self):
        subset = pd.DataFrame(
            [facility(f"r{i}", CLASS_NEEDS_REVIEW, evidence=50) for i in range(9)]
        )
        first = select_priority_facilities(subset, REGION_NEEDS_REVIEW)
        second = select_priority_facilities(subset, REGION_NEEDS_REVIEW)
        assert len(first) == 5
        assert first["unique_id"].tolist() == second["unique_id"].tolist()

    def test_never_mutates_input(self):
        subset = pd.DataFrame([facility("a", CLASS_NEEDS_REVIEW)])
        before = subset.copy(deep=True)
        select_priority_facilities(subset, REGION_NEEDS_REVIEW)
        pd.testing.assert_frame_equal(subset, before)


class TestFlags:
    def test_humanize(self):
        assert (
            humanize_flag("directory_or_partner_content_detected")
            == "Directory or partner content detected"
        )

    def test_primary_flag(self):
        row = facility("a", CLASS_TRUSTED, flag_names=["icu_claim_uncorroborated"])
        assert primary_flag(row) == "Icu claim uncorroborated"
        assert primary_flag(facility("b", CLASS_TRUSTED)) is None


class TestHeroCountsHtml:
    SUMMARY = {
        "facility_count": 10077,
        "trusted_icu_count": 203,
        "needs_review_count": 2867,
        "likely_gap_count": 6890,
        "insufficient_data_count": 117,
    }

    def test_uses_html_strong_never_markdown_bold(self):
        html = hero_counts_html(self.SUMMARY)
        # Markdown is not interpreted inside unsafe_allow_html blocks, so a
        # literal ** would render as visible asterisks.
        assert "**" not in html
        for count in (10077, 203, 2867, 6890, 117):
            assert f"<strong>{count}</strong>" in html

    def test_labels_and_icons_preserved(self):
        html = hero_counts_html(self.SUMMARY)
        for token in (
            "supplied records",
            "🟢",
            "trusted evidence",
            "🟡",
            "need review",
            "🔴",
            "show no ICU evidence",
            "⚪",
            "insufficient",
        ):
            assert token in html

    def test_missing_keys_default_to_zero(self):
        assert "<strong>0</strong>" in hero_counts_html({})


class TestDistribution:
    def test_counts_and_percentages(self):
        subset = pd.DataFrame(
            [facility("a", CLASS_TRUSTED), facility("b", CLASS_LIKELY_GAP), facility("c", CLASS_LIKELY_GAP)]
        )
        rows = status_distribution(subset)
        by_class = {r["classification"]: r for r in rows}
        assert by_class[CLASS_TRUSTED]["count"] == 1
        assert by_class[CLASS_LIKELY_GAP]["pct"] == 66.7
        assert by_class[CLASS_INSUFFICIENT]["count"] == 0
        assert len(rows) == 4  # stable order, all four statuses present
        assert by_class[CLASS_LIKELY_GAP]["label"] == "No ICU evidence in judgeable record"

    def test_empty_subset(self):
        rows = status_distribution(pd.DataFrame({"classification": []}))
        assert all(r["count"] == 0 for r in rows)


class TestFacilityContext:
    def test_expected_classifications(self):
        from caregap_map.ui_components import (
            CONTEXT_GENERAL,
            CONTEXT_OUTPATIENT,
            CONTEXT_SPECIALTY,
            CONTEXT_UNKNOWN,
            facility_context,
        )

        cases = {
            "Multispeciality Hospital": CONTEXT_GENERAL,
            "General Hospital": CONTEXT_GENERAL,
            "Medical College Hospital": CONTEXT_GENERAL,
            "Sunrise Hospital": CONTEXT_GENERAL,  # unqualified hospital name
            "Eye Hospital": CONTEXT_SPECIALTY,
            "Kidney Hospital & Stone Clinic": CONTEXT_SPECIALTY,
            "Dental Clinic": CONTEXT_OUTPATIENT,
            "Tooth Care Clinic": CONTEXT_OUTPATIENT,
            "Path Lab": CONTEXT_OUTPATIENT,
            "Pathology Lab": CONTEXT_OUTPATIENT,
            "Diagnostic Centre": CONTEXT_OUTPATIENT,
            "Apollo Pharmacy": CONTEXT_OUTPATIENT,
            "Dr. A. K. Sharma": CONTEXT_OUTPATIENT,
            "The Smile World": CONTEXT_UNKNOWN,  # ambiguous stays unknown
            "": CONTEXT_UNKNOWN,
            None: CONTEXT_UNKNOWN,
        }
        for name, expected in cases.items():
            assert facility_context(name) == expected, name

    def test_context_never_changes_scoring_or_classification(self):
        from caregap_map.scoring import score_facility
        from caregap_map.ui_components import facility_context

        record = {
            "name": "City Path Lab",
            "description": "A diagnostic laboratory.",
            "procedure": '["blood tests"]',
            "equipment": '["analyser"]',
            "source_urls": '["https://example.org"]',
            "latitude": "10.0",
            "longitude": "76.0",
        }
        before = score_facility(record)
        facility_context(record["name"], "facility")
        after = score_facility(record)
        assert after.classification == before.classification
        assert after.capability_evidence_score == before.capability_evidence_score
        assert after.data_completeness_score == before.data_completeness_score


class TestContextAwarePriority:
    def test_hospital_like_records_outrank_labs_and_dentists(self):
        subset = pd.DataFrame(
            [
                facility("lab", CLASS_NEEDS_REVIEW, evidence=90) | {"name": "City Path Lab"},
                facility("dental", CLASS_NEEDS_REVIEW, evidence=88) | {"name": "Pearl Dental Clinic"},
                facility("hosp", CLASS_NEEDS_REVIEW, evidence=50) | {"name": "Shelar Hospital"},
                facility("eye", CLASS_NEEDS_REVIEW, evidence=60) | {"name": "City Eye Hospital"},
            ]
        )
        priority = select_priority_facilities(subset, REGION_NEEDS_REVIEW)
        ids = priority["unique_id"].tolist()
        # General hospital first, then specialty hospital, then outpatient
        # by evidence - context ranks before raw evidence within a tier.
        assert ids == ["hosp", "eye", "lab", "dental"]
        assert priority.iloc[0]["facility_context"] == "Likely general-hospital context"

    def test_outpatient_records_remain_in_the_data(self):
        subset = pd.DataFrame(
            [
                facility("lab", CLASS_NEEDS_REVIEW, evidence=90) | {"name": "City Path Lab"},
                facility("hosp", CLASS_NEEDS_REVIEW, evidence=50) | {"name": "Shelar Hospital"},
            ]
        )
        before = subset.copy(deep=True)
        priority = select_priority_facilities(subset, REGION_NEEDS_REVIEW)
        pd.testing.assert_frame_equal(subset, before)  # never mutated/dropped
        assert set(priority["unique_id"]) == {"lab", "hosp"}  # still listed, just later


class TestMapView:
    def test_identical_coordinates_get_max_zoom_without_crashing(self):
        from caregap_map.ui_components import MAP_MAX_ZOOM, map_view

        lat, lon, zoom = map_view([10.0, 10.0, 10.0], [76.0, 76.0, 76.0])
        assert (lat, lon) == (10.0, 76.0)
        assert zoom == MAP_MAX_ZOOM

    def test_single_facility(self):
        from caregap_map.ui_components import MAP_MAX_ZOOM, map_view

        assert map_view([10.0], [76.0])[2] == MAP_MAX_ZOOM

    def test_wide_spread_clamps_to_min_zoom(self):
        from caregap_map.ui_components import MAP_MIN_ZOOM, map_view

        assert map_view([8.0, 33.0], [70.0, 95.0])[2] == MAP_MIN_ZOOM

    def test_center_is_extent_midpoint_and_inputs_unmodified(self):
        from caregap_map.ui_components import map_view

        lats, lons = [10.0, 12.0], [70.0, 74.0]
        lat, lon, zoom = map_view(lats, lons)
        assert (lat, lon) == (11.0, 72.0)
        assert lats == [10.0, 12.0] and lons == [70.0, 74.0]  # never jittered
        assert map_view(lats, lons) == (lat, lon, zoom)  # deterministic

    def test_marker_style_is_visible(self):
        from caregap_map.ui_components import MAP_MARKER

        assert 12 <= MAP_MARKER["size"] <= 15
        assert MAP_MARKER["opacity"] >= 0.8


class TestFacilityMix:
    def test_counts_and_sentence(self):
        from caregap_map.ui_components import facility_mix_counts, facility_mix_sentence

        subset = pd.DataFrame(
            [
                facility("a", CLASS_LIKELY_GAP) | {"name": "General Hospital"},
                facility("b", CLASS_LIKELY_GAP) | {"name": "Eye Hospital"},
                facility("c", CLASS_LIKELY_GAP) | {"name": "Path Lab"},
                facility("d", CLASS_LIKELY_GAP) | {"name": "Mystery Place"},
            ]
        )
        counts = facility_mix_counts(subset)
        sentence = facility_mix_sentence(counts, len(subset))
        assert sentence == (
            "Of 4 supplied records, 1 appear to be general-hospital contexts, "
            "1 specialty contexts, 1 outpatient/diagnostic contexts and 1 unknown."
        )

    def test_gap_warning_only_when_relevant(self):
        from caregap_map.ui_components import (
            CONTEXT_GENERAL,
            CONTEXT_OUTPATIENT,
            mix_warning_applies,
        )

        assert mix_warning_applies({CONTEXT_OUTPATIENT: 3, CONTEXT_GENERAL: 1})
        assert mix_warning_applies({CONTEXT_OUTPATIENT: 1, CONTEXT_GENERAL: 0})
        assert not mix_warning_applies({CONTEXT_OUTPATIENT: 1, CONTEXT_GENERAL: 4})
        assert not mix_warning_applies({CONTEXT_OUTPATIENT: 0, CONTEXT_GENERAL: 0})


class TestDistrictCentroids:
    def _scored(self):
        def rec(uid, state, district, lat, lon, coord_status="ok"):
            return {
                "unique_id": uid,
                "state_final": state,
                "district_final": district,
                "lat_parsed": lat,
                "lon_parsed": lon,
                "coord_status": coord_status,
            }

        return pd.DataFrame(
            [
                rec("a1", "A", "D1", 10.0, 70.0),
                rec("a2", "A", "D1", 12.0, 72.0),
                rec("a3", "A", "D1", 11.0, 71.0),
                rec("a4", "A", "D1", 99.0, 99.0, coord_status="out_of_range"),  # ignored
                rec("b1", "B", "D2", 20.0, 80.0),
                rec("c1", "C", "D3", None, None, coord_status="missing"),  # never located
                rec("u1", None, None, 15.0, 75.0),  # unassigned - excluded
            ]
        )

    def _regions(self):
        def region(state, district, status, count):
            return {
                "state": state,
                "district": district,
                "region_status": status,
                "facility_count": count,
                "trusted_icu_count": 1,
                "needs_review_count": 2,
                "likely_gap_count": 3,
                "insufficient_data_count": 0,
                "pct_sufficient_data": 90.0,
            }

        return pd.DataFrame(
            [
                region("A", "D1", REGION_NEEDS_REVIEW, 4),
                region("B", "D2", REGION_TRUSTED, 1),
                region("C", "D3", REGION_DATA_DESERT, 1),  # no located records -> off-map
                region("(unassigned)", "(unassigned)", REGION_DATA_DESERT, 99),
            ]
        )

    def test_centroid_is_median_of_located_coordinates(self):
        from caregap_map.ui_components import district_centroids

        out = district_centroids(self._scored(), self._regions())
        d1 = out[out["district"] == "D1"].iloc[0]
        assert d1["lat"] == 11.0 and d1["lon"] == 71.0  # median, outlier row ignored
        assert d1["region_status"] == REGION_NEEDS_REVIEW
        assert d1["facility_count"] == 4

    def test_unlocated_and_unassigned_districts_are_excluded(self):
        from caregap_map.ui_components import district_centroids

        out = district_centroids(self._scored(), self._regions())
        assert set(out["district"]) == {"D1", "D2"}
        assert "(unassigned)" not in set(out["state"])

    def test_deterministic_order_and_join_columns(self):
        from caregap_map.ui_components import district_centroids

        a = district_centroids(self._scored(), self._regions())
        b = district_centroids(self._scored(), self._regions())
        pd.testing.assert_frame_equal(a, b)
        for col in ("region_status", "facility_count", "pct_sufficient_data"):
            assert col in a.columns


class TestExampleRegions:
    def _regions(self):
        def region(state, district, status, count):
            return {
                "state": state,
                "district": district,
                "region_status": status,
                "facility_count": count,
            }

        return pd.DataFrame(
            [
                region("A", "Desert1", REGION_DATA_DESERT, 4),
                region("A", "Desert2", REGION_DATA_DESERT, 9),
                region("B", "Gap1", REGION_PLANNING_GAP, 7),
                region("C", "Rev1", REGION_NEEDS_REVIEW, 12),
                region("(unassigned)", "X", REGION_PLANNING_GAP, 99),
            ]
        )

    def test_examples_come_from_current_data(self):
        examples = example_regions(self._regions())
        assert examples["Data desert"] == ("A", "Desert2")  # most records wins
        assert examples["Potential planning gap"] == ("B", "Gap1")  # unassigned excluded
        assert examples["Needs facility verification"] == ("C", "Rev1")

    def test_missing_status_is_simply_absent(self):
        regions = self._regions()
        regions = regions[regions["region_status"] != REGION_DATA_DESERT]
        examples = example_regions(regions)
        assert "Data desert" not in examples
        assert len(examples) == 2

    def test_planning_gap_example_prefers_hospital_rich_district(self):
        def region(state, district, status, count):
            return {
                "state": state,
                "district": district,
                "region_status": status,
                "facility_count": count,
            }

        regions = pd.DataFrame(
            [
                # LabTown has more records but is dominated by labs/dentists.
                region("A", "LabTown", REGION_PLANNING_GAP, 8),
                region("A", "HospTown", REGION_PLANNING_GAP, 6),
            ]
        )

        def rec(uid, district, name, coord_status="ok"):
            return {
                "unique_id": uid,
                "state_final": "A",
                "district_final": district,
                "name": name,
                "organization_type": "facility",
                "coord_status": coord_status,
            }

        scored = pd.DataFrame(
            [rec(f"l{i}", "LabTown", f"Path Lab {i}") for i in range(6)]
            + [rec("l6", "LabTown", "Dental Clinic"), rec("l7", "LabTown", "Some Hospital")]
            + [rec(f"h{i}", "HospTown", f"General Hospital {i}") for i in range(4)]
            + [rec("h4", "HospTown", "Eye Hospital"), rec("h5", "HospTown", "City Path Lab")]
        )
        examples = example_regions(regions, scored)
        assert examples["Potential planning gap"] == ("A", "HospTown")
        # Deterministic and unchanged on repeat calls.
        assert example_regions(regions, scored) == examples
        # Fallback without scored data: most records wins (old behavior).
        assert example_regions(regions)["Potential planning gap"] == ("A", "LabTown")
