"""Resumable offline Codex CLI batch extraction over the facility dataset.

Pilot (24 records):
    python scripts/run_codex_extraction.py --limit 24 --batch-size 6 \
        --model gpt-5.6-luna --reasoning-effort low

Resume after interruption:
    python scripts/run_codex_extraction.py --resume --model gpt-5.6-luna

Requires a ChatGPT-authenticated Codex CLI (`codex login`); never uses
OPENAI_API_KEY. See docs/CODEX_EXTRACTION.md. Refuses more than
500 records without --yes. The Streamlit app never runs this.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.codex_extraction import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    BatchProcessor,
    CodexAuthError,
    CodexNotInstalledError,
    CodexRunner,
    CodexRunStore,
    check_resume_compatible,
    codex_cli_version,
    new_manifest,
    partition_valid_ids,
    repo_commit_sha,
    sha256_file,
)
from caregap_map.config import load_scoring_config  # noqa: E402

CONFIRM_THRESHOLD = 500
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "config" / "codex_icu_output_schema.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=24, help="Records to process (default 24)")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--resume", action="store_true", help="Continue an interrupted run")
    parser.add_argument("--force-resume", action="store_true", help="Resume despite config mismatch")
    parser.add_argument("--restart", action="store_true", help="Archive the previous run and start fresh")
    parser.add_argument("--dry-run", action="store_true", help="Plan batches; no Codex calls")
    parser.add_argument("--yes", action="store_true", help=f"Confirm runs over {CONFIRM_THRESHOLD} records")
    parser.add_argument("--state", default=None, help="Filter to one state (state_final)")
    parser.add_argument("--input-path", default="data/processed/facilities_scored.parquet")
    parser.add_argument("--output-dir", default="data/processed/codex_extraction")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=600.0, help="Seconds per codex exec")
    parser.add_argument(
        "--selection",
        choices=["stratified", "sorted"],
        default="stratified",
        help="stratified (default): even per-classification sample, comparable with the "
        "OpenAI pilot; sorted: plain unique_id order (use for full sweeps)",
    )
    return parser.parse_args(argv)


def select_records(
    df: pd.DataFrame, state: str | None, limit: int, skip_ids: set[str], selection: str = "stratified"
) -> list[dict]:
    """Deterministic selection of unprocessed records.

    Stratified mode draws evenly across the deterministic classifications
    (sorted by unique_id within each class), matching run_llm_extraction.py so
    pilot samples overlap across extractors. Falls back to plain sorted order
    when the limit covers everything.
    """
    if state:
        df = df[df["state_final"] == state]
    df = df[~df["unique_id"].isin(skip_ids)].sort_values("unique_id")
    if selection == "sorted" or limit >= len(df):
        return df.head(limit).to_dict("records")
    per_class = max(1, limit // max(1, df["classification"].nunique()))
    parts = [group.head(per_class) for _, group in df.groupby("classification")]
    sample = pd.concat(parts)
    if len(sample) < limit:  # top up from the remainder, still deterministic
        rest = df[~df["unique_id"].isin(sample["unique_id"])]
        sample = pd.concat([sample, rest.head(limit - len(sample))])
    return sample.sort_values("unique_id").head(limit).to_dict("records")


def build_outputs(store: CodexRunStore, source_df: pd.DataFrame, runtime_s: float, manifest: dict) -> None:
    """facilities_scored_codex.parquet + codex_comparison.json (agreement != accuracy)."""
    completed = store.load_completed()
    if not completed:
        print("No completed records; skipping output build.")
        return
    codex_df = pd.DataFrame(
        [
            {
                "unique_id": r["unique_id"],
                "codex_classification": r["classification"],
                "codex_evidence_score": r["capability_evidence_score"],
                "codex_completeness_score": r["data_completeness_score"],
                "codex_explicit_claim": r["explicit_icu_claim"],
                "codex_icu_subtypes_json": json.dumps(r.get("icu_subtypes", [])),
                "codex_fragments_json": json.dumps(r.get("fragments", [])),
                "codex_flags_json": json.dumps(r.get("validation_flags", [])),
                "model": r.get("model"),
                "batch_id": r.get("batch_id"),
            }
            for r in completed
        ]
    ).drop_duplicates("unique_id", keep="first")

    det = source_df[["unique_id", "classification", "capability_evidence_score", "icu_subtypes_json"]].rename(
        columns={"classification": "det_classification", "capability_evidence_score": "det_evidence_score"}
    )
    merged = codex_df.merge(det, on="unique_id", how="left")

    openai_path = Path("data/processed/facilities_scored_llm.parquet")
    openai_agreement = None
    if openai_path.exists():
        openai_df = pd.read_parquet(openai_path)[["unique_id", "llm_classification"]].dropna()
        merged = merged.merge(openai_df, on="unique_id", how="left")
        both = merged[merged["llm_classification"].notna()]
        if len(both):
            openai_agreement = round(
                100.0 * (both["codex_classification"] == both["llm_classification"]).mean(), 1
            )

    merged.to_parquet(store.root / "facilities_scored_codex.parquet", index=False)

    errors = store._read_jsonl(store.errors_path)
    subtype_counts: dict[str, int] = {}
    for r in completed:
        for s in r.get("icu_subtypes", []):
            subtype_counts[s] = subtype_counts.get(s, 0) + 1
    comparison = {
        "note": (
            "Agreement is DIAGNOSTIC, not accuracy; the deterministic baseline is not "
            "ground truth. All three columns share deterministic verification/scoring."
        ),
        "model": manifest["model"],
        "reasoning_effort": manifest["reasoning_effort"],
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "records_requested": manifest["requested_records"],
        "records_completed": len(codex_df),
        "records_failed": len(errors),
        "error_rate_pct": round(100.0 * len(errors) / max(1, manifest["requested_records"]), 1),
        "agreement_with_deterministic_pct": round(
            100.0 * (merged["codex_classification"] == merged["det_classification"]).mean(), 1
        ),
        "agreement_with_openai_pct": openai_agreement,
        "evidence_score_mean_abs_diff": round(
            float((merged["codex_evidence_score"] - merged["det_evidence_score"]).abs().mean()), 1
        ),
        "verified_fragments": sum(r.get("verified_fragments", 0) for r in completed),
        "dropped_fragments": sum(r.get("dropped_fragments", 0) for r in completed),
        "low_information_fragments": sum(r.get("low_information_fragments", 0) for r in completed),
        "subtype_distribution": subtype_counts,
        "disagreements_with_deterministic": merged.loc[
            merged["codex_classification"] != merged["det_classification"],
            ["unique_id", "det_classification", "codex_classification"],
        ].to_dict("records"),
        "runtime_s": round(runtime_s, 1),
        "avg_runtime_per_record_s": round(runtime_s / max(1, len(codex_df)), 2),
        "batches_executed": manifest.get("batches_executed", 0),
        "retries": manifest.get("retries", 0),
        "codex_executions": manifest.get("codex_executions", 0),
    }
    (store.root / "codex_comparison.json").write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in comparison.items() if k != "disagreements_with_deterministic"}, indent=2)
    )
    print(f"Outputs written under {store.root}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_scoring_config()

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found - run scripts/build_processed_data.py.", file=sys.stderr)
        return 1
    source_hash = sha256_file(input_path)
    source_df = pd.read_parquet(input_path)

    store = CodexRunStore(args.output_dir)
    if args.restart:
        archived = store.archive()
        if archived:
            print(f"Previous run archived to {archived}")

    manifest = store.load_manifest()
    completed_ids: set[str] = set()
    if args.resume:
        if manifest is None:
            print("ERROR: --resume but no manifest found; start a fresh run.", file=sys.stderr)
            return 1
        problems = check_resume_compatible(manifest, model=args.model, source_hash=source_hash)
        if problems and not args.force_resume:
            print(
                "ERROR: unsafe resume (use --force-resume to override):\n  - " + "\n  - ".join(problems),
                file=sys.stderr,
            )
            return 1
        completed_ids = store.completed_ids()
        print(f"Resuming run {manifest['run_id']}: {len(completed_ids)} records already completed.")
    elif manifest is not None and not args.dry_run:
        print(
            "ERROR: an existing run is present in "
            f"{store.root} - use --resume to continue or --restart to archive it.",
            file=sys.stderr,
        )
        return 1

    records = select_records(source_df, args.state, args.limit, completed_ids, args.selection)

    # Corrupted column-shifted rows are quarantined before any Codex call -
    # the model cannot echo their unique_id and would waste a full
    # retry/split ladder per record.
    records, corrupted = partition_valid_ids(records)
    n_fresh_quarantined = 0
    if corrupted:
        from datetime import UTC, datetime

        known_errors = {r.get("unique_id") for r in store._read_jsonl(store.errors_path)}
        fresh = [r for r in corrupted if str(r["unique_id"]) not in known_errors]
        n_fresh_quarantined = len(fresh)
        for rec in fresh:
            store.append_error(
                {
                    "unique_id": str(rec["unique_id"]),
                    "error": "corrupted_unique_id_skipped (column-shifted source row; unjudgeable)",
                    "model": args.model,
                    "prompt_version": PROMPT_VERSION,
                    "failed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
        print(
            f"Pre-quarantined {len(corrupted)} corrupted-id records "
            f"({len(fresh)} newly written to errors.jsonl); no Codex calls spent on them."
        )

    if not records:
        print("Nothing to do - all requested records are already completed.")
        build_outputs(store, source_df, 0.0, manifest or {})
        return 0
    if len(records) > CONFIRM_THRESHOLD and not args.yes:
        print(
            f"ERROR: {len(records)} records exceed the {CONFIRM_THRESHOLD}-record guard; "
            "re-run with --yes to confirm subscription usage.",
            file=sys.stderr,
        )
        return 1

    batches = [records[i : i + args.batch_size] for i in range(0, len(records), args.batch_size)]
    est_execs = len(batches)
    print(
        f"Plan: {len(records)} records in {len(batches)} batches of <= {args.batch_size} "
        f"via {args.model} (reasoning={args.reasoning_effort}, timeout={args.timeout:.0f}s, "
        f">= {est_execs} codex executions)"
    )
    if args.dry_run:
        print("--dry-run: no Codex calls made. First batch ids:")
        for rec in batches[0][:10]:
            print("  ", rec["unique_id"])
        return 0

    if manifest is None:
        manifest = new_manifest(
            source_path=str(input_path),
            source_hash=source_hash,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            batch_size=args.batch_size,
            timeout=args.timeout,
            requested=len(records),
            codex_version=codex_cli_version(),
            repo_commit=repo_commit_sha(),
        )
    else:
        manifest["requested_records"] = manifest.get("requested_records", 0) + len(records)
    manifest["failed_records"] = manifest.get("failed_records", 0) + n_fresh_quarantined
    store.save_manifest(manifest)

    processor = BatchProcessor(
        CodexRunner(),
        store,
        config,
        SCHEMA_PATH,
        args.model,
        args.reasoning_effort,
        args.timeout,
        max_retries=args.max_retries,
    )
    # Continue batch numbering after any existing batch files (incl. retries).
    processor.batch_counter = len(list(store.batches_dir.glob("*.input.json")))

    started = time.monotonic()
    succeeded = failed = 0
    try:
        for index, batch in enumerate(batches, 1):
            ok, bad = processor.process(batch)
            succeeded += ok
            failed += bad
            manifest["succeeded_records"] = manifest.get("succeeded_records", 0) + ok
            manifest["failed_records"] = manifest.get("failed_records", 0) + bad
            manifest.setdefault("completed_batches", []).append(index)
            manifest["batches_executed"] = manifest.get("batches_executed", 0) + 1
            manifest["retries"] = processor.retries
            manifest["codex_executions"] = manifest.get("codex_executions", 0) + processor.executions
            processor.executions = 0
            store.save_manifest(manifest)
            print(f"batch {index}/{len(batches)}: ok={ok} failed={bad} (total ok={succeeded})")
    except CodexNotInstalledError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except CodexAuthError as exc:
        store.save_manifest(manifest)
        print(f"\nPAUSED: {exc}\nProgress is saved; continue later with --resume.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        store.save_manifest(manifest)
        print("\nInterrupted; progress is saved. Continue with --resume.", file=sys.stderr)
        return 130

    runtime = time.monotonic() - started
    print(f"\nDone: {succeeded} succeeded, {failed} quarantined, {runtime:.0f}s.")
    build_outputs(store, source_df, runtime, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
