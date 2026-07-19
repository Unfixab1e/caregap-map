"""MLflow evaluation: sample builder, tracing via a fake mlflow, failure path.

None of these tests require mlflow, Databricks credentials, network access
or real data - mlflow is simulated with an in-memory fake.
"""

from __future__ import annotations

import contextlib
import json

import pandas as pd
import pytest

from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    ScoringConfig,
)
from caregap_map.mlflow_evaluation import (
    MISSING_MLFLOW_HELP,
    aggregate_results,
    build_trace_sample,
    require_mlflow,
    trace_facility,
)
from caregap_map.scoring import score_facility


def make_row(uid: str, cls: str, name: str = "Test Hospital", subtypes: list | None = None) -> dict:
    return {
        "unique_id": uid,
        "name": name,
        "organization_type": "facility",
        "classification": cls,
        "icu_subtypes_json": json.dumps(subtypes or []),
        "description": "A facility.",
        "procedure": "[]",
        "equipment": "[]",
        "capability": "[]",
        "specialties": "[]",
        "source_urls": '["https://example.org"]',
        "latitude": "10.0",
        "longitude": "76.0",
        "capacity": None,
        "numberDoctors": None,
    }


class TestBuildTraceSample:
    def _scored(self) -> pd.DataFrame:
        rows = [make_row(f"t{i}", CLASS_TRUSTED) for i in range(10)]
        rows += [make_row(f"g{i}", CLASS_LIKELY_GAP, name=f"Dental {i}") for i in range(10)]
        rows += [make_row(f"r{i}", CLASS_NEEDS_REVIEW) for i in range(10)]
        rows += [make_row(f"i{i}", CLASS_INSUFFICIENT) for i in range(10)]
        rows.append(make_row("nicu1", CLASS_TRUSTED, subtypes=["neonatal_icu"]))
        return pd.DataFrame(rows)

    def test_covers_classes_subtypes_and_disagreements(self):
        scored = self._scored()
        sample = build_trace_sample(
            scored,
            llm_by_id={"r9": CLASS_TRUSTED},  # disagreement (stored: Needs Review)
            codex_by_id={"i9": CLASS_LIKELY_GAP},  # disagreement (stored: Insufficient)
            labelled_ids={"t9"},
            per_class=2,
        )
        ids = set(sample["unique_id"])
        assert {"r9", "i9", "t9", "nicu1"} <= ids
        for cls in (CLASS_TRUSTED, CLASS_LIKELY_GAP, CLASS_NEEDS_REVIEW, CLASS_INSUFFICIENT):
            assert (sample["classification"] == cls).any()

    def test_hard_cap(self):
        sample = build_trace_sample(self._scored(), per_class=20, max_sample=15)
        assert len(sample) == 15

    def test_deterministic(self):
        a = build_trace_sample(self._scored(), per_class=3)["unique_id"].tolist()
        b = build_trace_sample(self._scored(), per_class=3)["unique_id"].tolist()
        assert a == b


class FakeSpan:
    def __init__(self, name: str):
        self.name = name
        self.attributes: dict = {}

    def set_attributes(self, attrs: dict):
        self.attributes.update(attrs)


class FakeMlflow:
    """Minimal stand-in for the mlflow API surface the module uses."""

    def __init__(self):
        self.spans: list[FakeSpan] = []
        self.metrics: dict = {}
        self.params: dict = {}
        self.dicts: list = []

    @contextlib.contextmanager
    def start_span(self, name: str):
        span = FakeSpan(name)
        self.spans.append(span)
        yield span

    def set_experiment(self, name):
        self.experiment = name

    @contextlib.contextmanager
    def start_run(self, run_name=None):
        yield self

    def log_params(self, params):
        self.params.update(params)

    def log_metrics(self, metrics):
        self.metrics.update(metrics)

    def log_metric(self, key, value):
        self.metrics[key] = value

    def log_dict(self, payload, name):
        self.dicts.append((name, payload))


class TestTraceFacility:
    def test_spans_and_parity_with_production_scoring(self):
        record = {
            "unique_id": "abc",
            "name": "Sunrise Hospital",
            "description": "24x7 ICU with ventilator support and 10 ICU beds.",
            "procedure": '["intensive care"]',
            "equipment": '["ventilator"]',
            "capability": "[]",
            "specialties": "[]",
            "source_urls": '["https://example.org"]',
            "latitude": "10.0",
            "longitude": "76.0",
            "capacity": "120",
            "numberDoctors": "12",
        }
        fake = FakeMlflow()
        config = ScoringConfig()
        row = trace_facility(
            fake,
            record,
            config,
            stored_classification=CLASS_TRUSTED,
            openai_classification=CLASS_TRUSTED,
            human_classification=CLASS_TRUSTED,
        )
        span_names = [s.name for s in fake.spans]
        for expected in (
            "facility_abc",
            "load_supplied_record",
            "deterministic_extraction",
            "exact_fragment_verification",
            "icu_subtype_detection",
            "validators",
            "evidence_category_calculation",
            "evidence_score",
            "completeness_score",
            "classification",
            "comparison",
        ):
            assert expected in span_names

        # The traced, stage-by-stage result equals production scoring.
        production = score_facility(record, config)
        assert row["classification"] == production.classification
        assert row["evidence_score"] == production.capability_evidence_score
        assert row["completeness_score"] == production.data_completeness_score
        assert row["latency_s"] >= 0

    def test_no_raw_record_text_in_span_attributes(self):
        record = {
            "unique_id": "abc",
            "name": "Sunrise Hospital",
            "description": "SECRETPHRASE inside a long description with ICU.",
            "procedure": "[]",
            "equipment": "[]",
            "capability": "[]",
            "specialties": "[]",
            "source_urls": "[]",
        }
        fake = FakeMlflow()
        trace_facility(fake, record, ScoringConfig())
        serialized = json.dumps([s.attributes for s in fake.spans], default=str)
        assert "SECRETPHRASE" not in serialized


class TestAggregate:
    def _row(self, cls, openai=None, codex=None, human=None):
        return {
            "unique_id": "x",
            "classification": cls,
            "evidence_score": 0,
            "completeness_score": 80,
            "explicit_claim": cls == CLASS_TRUSTED,
            "subtypes": ["neonatal_icu"] if cls == CLASS_TRUSTED else [],
            "flags": ["possible_duplicate_facility"] if cls == CLASS_TRUSTED else [],
            "verified_fragments": 2,
            "stored_classification": cls,
            "openai_classification": openai,
            "codex_classification": codex,
            "human_classification": human,
            "latency_s": 0.01,
        }

    def test_agreements_and_failure_modes(self):
        rows = [
            self._row(CLASS_TRUSTED, openai=CLASS_TRUSTED, human=CLASS_NEEDS_REVIEW),  # false trusted
            self._row(CLASS_LIKELY_GAP, codex=CLASS_LIKELY_GAP, human=CLASS_INSUFFICIENT),  # false gap
            self._row(CLASS_NEEDS_REVIEW, openai=CLASS_LIKELY_GAP),
        ]
        summary = aggregate_results(rows, errors=1, quarantined=2)
        assert summary["records_processed"] == 4
        assert summary["records_succeeded"] == 3
        assert summary["extraction_errors"] == 1
        assert summary["quarantined_records"] == 2
        assert summary["agreement_with_stored_deterministic_pct"] == 100.0
        assert summary["agreement_with_openai_pct"] == 50.0
        assert summary["agreement_with_codex_pct"] == 100.0
        assert summary["agreement_with_human_pct"] == 0.0
        assert summary["false_trusted_vs_human"] == 1
        assert summary["false_gap_vs_human"] == 1
        assert summary["subtype_counts"] == {"neonatal_icu": 1}
        assert summary["confusion_vs_human"][CLASS_NEEDS_REVIEW][CLASS_TRUSTED] == 1
        assert "DIAGNOSTIC" in summary["note"]

    def test_absent_comparisons_are_none_not_zero(self):
        summary = aggregate_results([self._row(CLASS_TRUSTED)], errors=0, quarantined=0)
        assert summary["agreement_with_human_pct"] is None
        assert summary["false_trusted_vs_human"] is None
        assert summary["agreement_with_openai_pct"] is None


class TestOptionality:
    def test_missing_mlflow_is_actionable(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_mlflow(name, *args, **kwargs):
            if name == "mlflow":
                raise ImportError("No module named 'mlflow'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_mlflow)
        with pytest.raises(RuntimeError) as err:
            require_mlflow()
        assert "OPTIONAL" in str(err.value)
        assert "pip install" in str(err.value)
        assert str(err.value) == MISSING_MLFLOW_HELP
