"""Run LLM evidence extraction on a facility sample and compare with the baseline.

Usage:
    python scripts/run_llm_extraction.py [--limit 24] [--state Kerala]
                                         [--data-dir data] [--model gpt-4o-mini]

Requires OPENAI_API_KEY (and `pip install -e ".[llm]"`). The sample is
stratified across the deterministic classifications so agreement is measured
on all four states, not just the easy majority class.

Outputs (git-ignored):
    <data-dir>/processed/facilities_scored_llm.parquet   per-record comparison
    <data-dir>/processed/llm_comparison.json             summary metrics

The LLM only replaces evidence *extraction*. Scores, validators and
classification remain deterministic for both columns of the comparison.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.config import DataPaths, load_env_file, load_scoring_config  # noqa: E402
from caregap_map.llm_extraction import (  # noqa: E402
    LlmEvidenceExtractor,
    OpenAiClient,
    estimate_cost_usd,
)
from caregap_map.scoring import score_facility  # noqa: E402

# Rough per-record token estimate for the pre-run budget preview (the real
# usage is measured from API responses and reported in the summary).
EST_INPUT_TOKENS_PER_RECORD = 1_800
EST_OUTPUT_TOKENS_PER_RECORD = 500
# Runs estimated above this need an explicit --yes (protects a small credit).
COST_CONFIRM_THRESHOLD_USD = 2.00


def stratified_sample(scored: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Deterministic sample with up to limit/4 records per classification."""
    per_class = max(1, limit // scored["classification"].nunique())
    parts = [group.sort_values("unique_id").head(per_class) for _, group in scored.groupby("classification")]
    return pd.concat(parts).head(limit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=24, help="Facilities to send to the LLM")
    parser.add_argument("--state", default=None, help="Restrict the sample to one state")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--model", default=None, help="Override the configured model name")
    parser.add_argument(
        "--yes",
        action="store_true",
        help=f"Confirm runs whose estimated cost exceeds ${COST_CONFIRM_THRESHOLD_USD:.2f}",
    )
    args = parser.parse_args()

    load_env_file()  # picks up OPENAI_API_KEY from .env (never overrides real env)
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set (env or .env); refusing to run.", file=sys.stderr)
        return 1

    paths = DataPaths(data_dir=Path(args.data_dir))
    if not paths.facilities_scored_parquet.exists():
        print("ERROR: run scripts/build_processed_data.py first.", file=sys.stderr)
        return 1

    config = load_scoring_config()
    if args.model:
        config.llm.model = args.model

    scored = pd.read_parquet(paths.facilities_scored_parquet)
    if args.state:
        scored = scored[scored["state_final"] == args.state]
        if scored.empty:
            print(f"ERROR: no facilities for state {args.state!r}.", file=sys.stderr)
            return 1

    sample = stratified_sample(scored, args.limit)

    est_cost = estimate_cost_usd(
        len(sample) * EST_INPUT_TOKENS_PER_RECORD,
        len(sample) * EST_OUTPUT_TOKENS_PER_RECORD,
        config.llm,
    )
    print(
        f"Planned: {len(sample)} facilities x {config.llm.model} "
        f"-> estimated cost ~${est_cost:.2f} "
        f"(at ${config.llm.input_cost_per_mtok}/M in, ${config.llm.output_cost_per_mtok}/M out)"
    )
    if est_cost > COST_CONFIRM_THRESHOLD_USD and not args.yes:
        print(
            f"ERROR: estimated cost ${est_cost:.2f} exceeds the "
            f"${COST_CONFIRM_THRESHOLD_USD:.2f} guard. Re-run with --yes to confirm, "
            "or lower --limit.",
            file=sys.stderr,
        )
        return 1

    client = OpenAiClient()
    extractor = LlmEvidenceExtractor(client, config)

    rows, errors = [], 0
    for _, record in sample.iterrows():
        base = {
            "unique_id": record["unique_id"],
            "name": record["name"],
            "state_final": record["state_final"],
            "det_classification": record["classification"],
            "det_evidence_score": record["capability_evidence_score"],
            "det_completeness_score": record["data_completeness_score"],
        }
        try:
            llm_score = score_facility(record.to_dict(), config, extractor=extractor.extract)
        except Exception as exc:  # noqa: BLE001 - includes LlmExtractionError; log and continue
            errors += 1
            rows.append({**base, "llm_error": str(exc)[:300]})
            print(f"  [error] {record['name']}: {exc}", file=sys.stderr)
            continue
        rows.append(
            {
                **base,
                "llm_classification": llm_score.classification,
                "llm_evidence_score": llm_score.capability_evidence_score,
                "llm_completeness_score": llm_score.data_completeness_score,
                "llm_explicit_claim": llm_score.evidence.explicit_icu_claim,
                "llm_fragments_json": json.dumps(
                    [f.model_dump() for f in llm_score.evidence.supporting_text_fragments]
                ),
                "llm_unclear_claims_json": json.dumps(llm_score.evidence.unclear_claims),
                "llm_explanation": llm_score.evidence.extraction_explanation,
                "llm_flags_json": json.dumps([f.model_dump() for f in llm_score.validation_flags]),
                "llm_error": None,
            }
        )
        print(
            f"  [ok] {record['name']}: det={record['classification']} "
            f"llm={llm_score.classification} "
            f"(spent so far ~${client.estimated_cost_usd(config.llm):.3f})"
        )

    out = pd.DataFrame(rows)
    ok = out[out["llm_error"].isna()] if "llm_error" in out else out
    summary = {
        "model": config.llm.model,
        "sampled": len(sample),
        "scored": len(ok),
        "errors": errors,
        "prompt_tokens": client.total_prompt_tokens,
        "completion_tokens": client.total_completion_tokens,
        "estimated_cost_usd": round(client.estimated_cost_usd(config.llm), 4),
        "classification_agreement_pct": (
            round(100.0 * (ok["det_classification"] == ok["llm_classification"]).mean(), 1)
            if len(ok)
            else None
        ),
        "evidence_score_mean_abs_diff": (
            round(float((ok["det_evidence_score"] - ok["llm_evidence_score"]).abs().mean()), 1)
            if len(ok)
            else None
        ),
        "disagreements": (
            ok.loc[
                ok["det_classification"] != ok["llm_classification"],
                ["unique_id", "name", "det_classification", "llm_classification"],
            ].to_dict("records")
            if len(ok)
            else []
        ),
        "note": "Both columns share deterministic scoring/validation; only extraction differs. "
        "Name-duplicate flags are not recomputed for the LLM column.",
    }

    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    out.to_parquet(paths.processed_dir / "facilities_scored_llm.parquet", index=False)
    (paths.processed_dir / "llm_comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
