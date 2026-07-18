"""Shared synthetic fixtures. Tests never depend on the raw challenge data."""

from __future__ import annotations

import json

import pytest

from caregap_map.config import ScoringConfig


def make_record(**overrides) -> dict:
    """A fully populated synthetic facility record (raw-string form).

    Defaults describe a well-documented hospital with NO ICU evidence;
    tests override individual fields to build each archetype.
    """
    record = {
        "unique_id": "test-0001",
        "name": "Synthetic General Hospital",
        "organization_type": "facility",
        "address_city": "Testville",
        "address_stateOrRegion": "Kerala",
        "address_zipOrPostcode": "682001",
        "description": "A general hospital offering outpatient consultations and maternity care.",
        "area": "urban",
        "numberDoctors": "12",
        "capacity": "100",
        "specialties": json.dumps(["generalMedicine", "obstetrics"]),
        "procedure": json.dumps(["Outpatient consultations", "Normal deliveries"]),
        "equipment": json.dumps(["X-ray machine", "Ultrasound scanner"]),
        "capability": json.dumps(["Offers outpatient care", "Maternity services available"]),
        "source": "synthetic",
        "source_urls": json.dumps(["https://example.org/hospital"]),
        "latitude": "9.9312",
        "longitude": "76.2673",
        "state_final": "Kerala",
        "coord_status": "ok",
        "geo_conflict": False,
    }
    record.update(overrides)
    return record


@pytest.fixture
def config() -> ScoringConfig:
    return ScoringConfig()
