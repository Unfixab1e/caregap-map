"""PIN directory aggregation and facility geo-assignment."""

import pandas as pd

from caregap_map.geography import aggregate_pin_directory, assign_geography


def pin_directory_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Two offices under one PIN: same state/district, different coords.
            {
                "pincode": "682001",
                "statename": "KERALA",
                "district": "ERNAKULAM",
                "latitude": "9.9",
                "longitude": "76.2",
            },
            {
                "pincode": "682001",
                "statename": "KERALA",
                "district": "ERNAKULAM",
                "latitude": "10.1",
                "longitude": "76.4",
            },
            # A PIN with an invalid coordinate that must not poison the mean.
            {
                "pincode": "110001",
                "statename": "DELHI",
                "district": "NEW DELHI",
                "latitude": "NA",
                "longitude": "NA",
            },
            {
                "pincode": "110001",
                "statename": "DELHI",
                "district": "NEW DELHI",
                "latitude": "28.6",
                "longitude": "77.2",
            },
        ]
    )


class TestPinAggregation:
    def test_one_row_per_pin(self):
        agg = aggregate_pin_directory(pin_directory_fixture())
        assert len(agg) == 2
        assert set(agg["pincode_clean"]) == {"682001", "110001"}

    def test_coordinates_averaged_over_valid_only(self):
        agg = aggregate_pin_directory(pin_directory_fixture()).set_index("pincode_clean")
        assert abs(agg.loc["682001", "pin_lat"] - 10.0) < 1e-9
        assert agg.loc["110001", "pin_lat"] == 28.6  # NA row excluded, not zeroed
        assert agg.loc["110001", "n_valid_coords"] == 1

    def test_state_canonicalised(self):
        agg = aggregate_pin_directory(pin_directory_fixture()).set_index("pincode_clean")
        assert agg.loc["682001", "pin_state"] == "Kerala"
        assert agg.loc["682001", "pin_district"] == "Ernakulam"


class TestGeoAssignment:
    def _facilities(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                # PIN resolves; messy state field disagrees -> conflict flag.
                {"address_zipOrPostcode": "682001", "address_stateOrRegion": "Tamil Nadu"},
                # No PIN; canonicalisable state field -> fallback.
                {"address_zipOrPostcode": None, "address_stateOrRegion": "Orissa"},
                # Nothing usable.
                {"address_zipOrPostcode": "12", "address_stateOrRegion": "Mumbai"},
            ]
        )

    def test_pin_wins_and_conflict_recorded(self):
        agg = aggregate_pin_directory(pin_directory_fixture())
        out = assign_geography(self._facilities(), agg)
        row = out.iloc[0]
        assert row["state_final"] == "Kerala"
        assert row["district_from_pin"] == "Ernakulam"
        assert row["geo_source"] == "pin_directory"
        assert bool(row["geo_conflict"]) is True

    def test_state_field_fallback(self):
        agg = aggregate_pin_directory(pin_directory_fixture())
        out = assign_geography(self._facilities(), agg)
        row = out.iloc[1]
        assert row["state_final"] == "Odisha"
        assert row["geo_source"] == "state_field"
        assert bool(row["geo_conflict"]) is False

    def test_unresolvable_stays_unresolved(self):
        agg = aggregate_pin_directory(pin_directory_fixture())
        out = assign_geography(self._facilities(), agg)
        row = out.iloc[2]
        assert pd.isna(row["state_final"])
        assert row["geo_source"] == "none"
