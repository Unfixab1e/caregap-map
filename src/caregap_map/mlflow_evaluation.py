"""Bounded MLflow 3 evaluation and tracing over a representative sample.

Offline quality-evaluation workflow, NOT a live-app dependency: the
deployed app never imports this module, and everything else in the code
base runs when mlflow is absent. One trace per sampled facility exposes
the full deterministic audit chain as spans (record load -> extraction ->
fragment verification -> subtypes -> validators -> evidence categories ->
scores -> classification -> comparison), and one MLflow run aggregates
the comparison metrics against the stored OpenAI / Codex outputs and
human labels where available.

Privacy: traces carry identifiers, counts, scores and label names only -
never full raw records, fragment text, API keys or note content.
Model-to-model agreement is DIAGNOSTIC, never accuracy (the deterministic
baseline is not ground truth).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import CAT_UNKNOWN, categorize_for_audit
from .codex_extraction import partition_valid_ids
from .config import CLASS_LIKELY_GAP, CLASS_TRUSTED, DataPaths, ScoringConfig
from .evidence import extract_evidence
from .scenarios import data_snapshot_id, scoring_config_fingerprint
from .scoring import (
    classify,
    compute_completeness_score,
    compute_evidence_score,
    count_corroboration_categories,
)
from .validator import SEV_CONTRADICTION, SEV_SUSPICIOUS, has_severity, validate_facility

DEFAULT_EXPERIMENT = "/Users/blubthefish@gmail.com/caregap-evaluation"
MAX_SAMPLE = 65
SUBTYPES = ["neonatal_icu", "pediatric_icu", "cardiac_icu", "medical_icu", "surgical_icu"]

MISSING_MLFLOW_HELP = (
    "mlflow is not available - the MLflow evaluation is OPTIONAL and nothing "
    "else depends on it. To run it: pip install -e \".[mlflow]\", authenticate "
    "against the Databricks workspace (databricks CLI profile or "
    "DATABRICKS_HOST/DATABRICKS_TOKEN) and set MLFLOW_TRACKING_URI=databricks "
    "(or databricks://<profile>). The app, tests and deterministic pipeline "
    "run unchanged without it."
)


def require_mlflow() -> Any:
    """Import mlflow or fail with one clear, actionable message."""
    try:
        import mlflow
    except ImportError as exc:
        raise RuntimeError(MISSING_MLFLOW_HELP) from exc
    return mlflow


# ---------------------------------------------------------------------------
# Sample selection (bounded, deterministic)
# ---------------------------------------------------------------------------


def build_trace_sample(
    scored: pd.DataFrame,
    llm_by_id: dict[str, str] | None = None,
    codex_by_id: dict[str, str] | None = None,
    labelled_ids: set[str] | None = None,
    per_class: int = 6,
    max_sample: int = MAX_SAMPLE,
) -> pd.DataFrame:
    """Representative facilities: classes, subtypes, audit categories,
    model disagreements and human-labelled records. Deterministic order,
    hard-capped at ``max_sample`` (never the full dataset)."""
    llm_by_id = llm_by_id or {}
    codex_by_id = codex_by_id or {}
    picked: list[pd.DataFrame] = []

    for cls in sorted(scored["classification"].unique()):
        picked.append(scored[scored["classification"] == cls].sort_values("unique_id").head(per_class))

    subtype_lists = scored["icu_subtypes_json"].fillna("[]").map(json.loads)
    for subtype in SUBTYPES:
        mask = subtype_lists.map(lambda subs, s=subtype: s in subs)
        picked.append(scored[mask].sort_values("unique_id").head(3))

    gaps = scored[scored["classification"] == CLASS_LIKELY_GAP]
    categories = gaps.apply(
        lambda r: categorize_for_audit(r.get("name"), r.get("organization_type")), axis=1
    )
    for category in sorted(categories.unique()):
        if category == CAT_UNKNOWN:
            continue
        picked.append(gaps[categories == category].sort_values("unique_id").head(3))

    for by_id in (llm_by_id, codex_by_id):
        ids = [uid for uid, cls in by_id.items() if isinstance(cls, str) and cls]
        dis = scored[scored["unique_id"].isin(ids)]
        picked.append(dis[dis["classification"] != dis["unique_id"].map(by_id)])

    if labelled_ids:
        picked.append(scored[scored["unique_id"].isin(labelled_ids)])

    sample = pd.concat(picked).drop_duplicates("unique_id").sort_values("unique_id")
    return sample.head(max_sample)


# ---------------------------------------------------------------------------
# Per-facility trace
# ---------------------------------------------------------------------------


def trace_facility(
    mlflow: Any,
    record: dict,
    config: ScoringConfig,
    stored_classification: str | None = None,
    openai_classification: str | None = None,
    codex_classification: str | None = None,
    human_classification: str | None = None,
) -> dict:
    """One MLflow trace re-running the deterministic chain stage by stage.

    Uses exactly the public pipeline functions in ``score_facility``'s
    order, so the traced result equals the production result (asserted by
    the tests and by the determinism metric).
    """
    uid = str(record.get("unique_id"))
    started = time.perf_counter()
    with mlflow.start_span(name=f"facility_{uid}") as root:
        root.set_attributes({"unique_id": uid, "extractor_mode": "deterministic"})

        with mlflow.start_span(name="load_supplied_record") as span:
            populated = sum(
                1
                for field in ("description", "capability", "specialties", "procedure", "equipment",
                              "capacity", "numberDoctors", "source_urls")
                if record.get(field) not in (None, "") and record.get(field) == record.get(field)
            )
            span.set_attributes({"unique_id": uid, "populated_key_fields": populated})

        with mlflow.start_span(name="deterministic_extraction") as span:
            evidence = extract_evidence(record, config)
            group_counts = Counter(f.group for f in evidence.supporting_text_fragments)
            span.set_attributes(
                {
                    "proposed_fragments": len(evidence.supporting_text_fragments),
                    "fragments_by_group": dict(group_counts),
                    "explicit_icu_claim": evidence.explicit_icu_claim,
                    "icu_bed_count": evidence.icu_bed_count or 0,
                }
            )

        with mlflow.start_span(name="exact_fragment_verification") as span:
            # Deterministic fragments are verbatim source windows by
            # construction; the drop counters exist for model extractors.
            span.set_attributes(
                {
                    "verified_fragments": len(evidence.supporting_text_fragments),
                    "dropped_unverified_fragments": 0,
                    "dropped_low_information_fragments": 0,
                }
            )

        with mlflow.start_span(name="icu_subtype_detection") as span:
            span.set_attributes({"icu_subtypes": list(evidence.icu_subtypes)})

        with mlflow.start_span(name="validators") as span:
            flags = validate_facility(record, evidence, config)
            span.set_attributes(
                {
                    "validation_flags": [f.name for f in flags],
                    "has_contradiction": has_severity(flags, SEV_CONTRADICTION),
                    "has_suspicious": has_severity(flags, SEV_SUSPICIOUS),
                }
            )

        with mlflow.start_span(name="evidence_category_calculation") as span:
            n_corroboration, corroboration = count_corroboration_categories(evidence, config)
            span.set_attributes({"corroboration_categories": corroboration})

        with mlflow.start_span(name="evidence_score") as span:
            evidence_score, ev_components = compute_evidence_score(evidence, config)
            span.set_attributes({"evidence_score": evidence_score, "components": ev_components})

        with mlflow.start_span(name="completeness_score") as span:
            completeness_score, comp_components = compute_completeness_score(record, config)
            span.set_attributes(
                {"completeness_score": completeness_score, "components": comp_components}
            )

        with mlflow.start_span(name="classification") as span:
            classification, reason = classify(
                evidence_score,
                completeness_score,
                has_contradiction=has_severity(flags, SEV_CONTRADICTION),
                has_suspicious=has_severity(flags, SEV_SUSPICIOUS),
                config=config,
                explicit_claim=evidence.explicit_icu_claim,
                corroboration_categories=n_corroboration,
            )
            span.set_attributes({"classification": classification, "reason": reason})

        with mlflow.start_span(name="comparison") as span:
            comparison = {
                "stored_deterministic": stored_classification,
                "openai": openai_classification,
                "codex": codex_classification,
                "human": human_classification,
            }
            span.set_attributes(
                {k: (v or "(absent)") for k, v in comparison.items()}
                | {"note": "model-to-model agreement is diagnostic, not accuracy"}
            )

    return {
        "unique_id": uid,
        "classification": classification,
        "evidence_score": evidence_score,
        "completeness_score": completeness_score,
        "explicit_claim": evidence.explicit_icu_claim,
        "subtypes": list(evidence.icu_subtypes),
        "flags": [f.name for f in flags],
        "verified_fragments": len(evidence.supporting_text_fragments),
        "stored_classification": stored_classification,
        "openai_classification": openai_classification,
        "codex_classification": codex_classification,
        "human_classification": human_classification,
        "latency_s": round(time.perf_counter() - started, 4),
    }


# ---------------------------------------------------------------------------
# Aggregation + run
# ---------------------------------------------------------------------------


def _agreement(rows: list[dict], key: str) -> tuple[float | None, int]:
    pairs = [(r["classification"], r[key]) for r in rows if r.get(key)]
    if not pairs:
        return None, 0
    return round(100.0 * sum(a == b for a, b in pairs) / len(pairs), 1), len(pairs)


def _confusion(rows: list[dict], key: str) -> dict:
    table: dict[str, dict[str, int]] = {}
    for r in rows:
        other = r.get(key)
        if not other:
            continue
        table.setdefault(other, {}).setdefault(r["classification"], 0)
        table[other][r["classification"]] += 1
    return table


def aggregate_results(rows: list[dict], errors: int, quarantined: int) -> dict:
    """JSON-safe aggregate summary; every count the eval contract asks for."""
    subtype_counts: Counter = Counter(s for r in rows for s in r["subtypes"])
    flag_counts: Counter = Counter(f for r in rows for f in r["flags"])
    det_agreement, det_n = _agreement(rows, "stored_classification")
    openai_agreement, openai_n = _agreement(rows, "openai_classification")
    codex_agreement, codex_n = _agreement(rows, "codex_classification")
    human_agreement, human_n = _agreement(rows, "human_classification")
    false_trusted = sum(
        1
        for r in rows
        if r.get("human_classification")
        and r["classification"] == CLASS_TRUSTED
        and r["human_classification"] != CLASS_TRUSTED
    )
    false_gap = sum(
        1
        for r in rows
        if r.get("human_classification")
        and r["classification"] == CLASS_LIKELY_GAP
        and r["human_classification"] != CLASS_LIKELY_GAP
    )
    return {
        "note": "Model-to-model agreement is DIAGNOSTIC, not accuracy.",
        "records_processed": len(rows) + errors,
        "records_succeeded": len(rows),
        "extraction_errors": errors,
        "quarantined_records": quarantined,
        "verified_fragment_count": sum(r["verified_fragments"] for r in rows),
        "unsupported_fragments_dropped": 0,
        "low_information_fragments_dropped": 0,
        "explicit_icu_claims": sum(1 for r in rows if r["explicit_claim"]),
        "subtype_counts": dict(subtype_counts),
        "validation_flag_counts": dict(flag_counts),
        "agreement_with_stored_deterministic_pct": det_agreement,  # determinism check
        "agreement_with_stored_deterministic_n": det_n,
        "agreement_with_openai_pct": openai_agreement,
        "agreement_with_openai_n": openai_n,
        "agreement_with_codex_pct": codex_agreement,
        "agreement_with_codex_n": codex_n,
        "agreement_with_human_pct": human_agreement,
        "agreement_with_human_n": human_n,
        "false_trusted_vs_human": false_trusted if human_n else None,
        "false_gap_vs_human": false_gap if human_n else None,
        "confusion_vs_openai": _confusion(rows, "openai_classification"),
        "confusion_vs_codex": _confusion(rows, "codex_classification"),
        "confusion_vs_human": _confusion(rows, "human_classification"),
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 4) if rows else None,
        "total_latency_s": round(sum(r["latency_s"] for r in rows), 2),
        # Deterministic re-scoring uses no model calls: the traced pipeline
        # itself consumes no tokens and costs nothing. Model outputs being
        # compared were produced offline in earlier recorded runs.
        "token_usage": 0,
        "estimated_cost_usd": 0.0,
    }


def run_evaluation(
    *,
    data_dir: str | Path = "data",
    experiment: str = DEFAULT_EXPERIMENT,
    per_class: int = 6,
    max_sample: int = MAX_SAMPLE,
    labels_path: str | Path | None = None,
    codex_parquet: str | Path | None = None,
    out_json: str | Path = "reports/mlflow_eval_summary.json",
    run_name: str | None = None,
    repo_commit: str = "",
    timestamp: str = "",
) -> dict:
    """Run the bounded evaluation against the configured MLflow backend."""
    mlflow = require_mlflow()
    config = ScoringConfig()
    paths = DataPaths(data_dir=Path(data_dir))
    scored = pd.read_parquet(paths.facilities_scored_parquet)

    def _classification_map(path: Path, column: str) -> dict[str, str]:
        if not path.exists():
            return {}
        df = pd.read_parquet(path)
        if column not in df.columns:
            return {}
        return {
            str(uid): str(cls)
            for uid, cls in zip(df["unique_id"], df[column], strict=True)
            if isinstance(cls, str) and cls
        }

    llm_by_id = _classification_map(
        paths.processed_dir / "facilities_scored_llm.parquet", "llm_classification"
    )
    codex_by_id = (
        _classification_map(Path(codex_parquet), "codex_classification") if codex_parquet else {}
    )

    human_by_id: dict[str, str] = {}
    if labels_path and Path(labels_path).exists():
        labels = pd.read_csv(labels_path, dtype=str)
        labelled = labels[
            labels["human_expected_classification"].notna()
            & (labels["human_expected_classification"].str.strip() != "")
        ]
        human_by_id = dict(
            zip(labelled["unique_id"], labelled["human_expected_classification"].str.strip(), strict=True)
        )

    sample = build_trace_sample(
        scored, llm_by_id, codex_by_id, set(human_by_id), per_class=per_class, max_sample=max_sample
    )
    records, corrupted = partition_valid_ids(sample.to_dict("records"))

    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(
            {
                "extractor_mode": "deterministic",
                "model": "deterministic (keyword pipeline; comparisons vs stored model outputs)",
                "reasoning_effort": "n/a",
                "prompt_version": "n/a (deterministic)",
                "schema_version": "n/a (deterministic)",
                "repo_commit": repo_commit or "unknown",
                "scoring_config_fingerprint": scoring_config_fingerprint(config),
                "data_snapshot": data_snapshot_id(scored),
                "sample_size": len(records),
                "sample_cap": max_sample,
                "timestamp": timestamp or "unknown",
                "agreement_semantics": "diagnostic, not accuracy",
            }
        )
        rows: list[dict] = []
        errors = 0
        for record in records:
            uid = str(record.get("unique_id"))
            try:
                rows.append(
                    trace_facility(
                        mlflow,
                        record,
                        config,
                        stored_classification=record.get("classification"),
                        openai_classification=llm_by_id.get(uid),
                        codex_classification=codex_by_id.get(uid),
                        human_classification=human_by_id.get(uid),
                    )
                )
            except Exception:  # count, continue - one bad record must not kill the run
                errors += 1
        summary = aggregate_results(rows, errors, quarantined=len(corrupted))

        numeric = {
            k: float(v)
            for k, v in summary.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None
        }
        mlflow.log_metrics(numeric)
        for subtype, count in summary["subtype_counts"].items():
            mlflow.log_metric(f"subtype_{subtype}", count)
        mlflow.log_dict(summary, "evaluation_summary.json")
        run_id = mlflow.active_run().info.run_id

    summary["mlflow_experiment"] = experiment
    summary["mlflow_run_id"] = run_id
    out_path = Path(out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
