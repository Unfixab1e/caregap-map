"""End-to-end over the committed synthetic sample: clean -> score -> aggregate.

Proves a fresh clone (without the raw challenge data) can exercise the whole
pipeline on data/samples/facilities_sample.csv.
"""

from pathlib import Path

import pandas as pd
import pytest

from caregap_map.aggregation import aggregate_regions
from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    ScoringConfig,
)
from caregap_map.scoring import score_dataframe

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "facilities_sample.csv"


@pytest.fixture(scope="module")
def scored() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE, dtype=str)
    scores = score_dataframe(df, ScoringConfig())
    return pd.concat([df, scores], axis=1)


def test_sample_covers_all_four_states(scored):
    by_id = scored.set_index("unique_id")["classification"]
    assert by_id["sample-0001"] == CLASS_TRUSTED  # strong, corroborated ICU
    assert by_id["sample-0002"] == CLASS_NEEDS_REVIEW  # claim without corroboration
    assert by_id["sample-0003"] == CLASS_LIKELY_GAP  # complete, no ICU evidence
    assert by_id["sample-0004"] == CLASS_INSUFFICIENT  # null-like placeholders only
    assert by_id["sample-0005"] == CLASS_NEEDS_REVIEW  # "no ICU" vs ICU claim
    assert by_id["sample-0006"] == CLASS_NEEDS_REVIEW  # 50-bed ICU in a 20-bed home


def test_sample_aggregation_runs(scored):
    scored = scored.assign(state_final=scored["address_stateOrRegion"], district_final=scored["address_city"])
    regions = aggregate_regions(scored, "district")
    assert len(regions) >= 4
    assert {"region_status", "trust_weighted_icu_coverage"} <= set(regions.columns)
