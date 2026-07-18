# Offline Codex CLI batch extraction

## Purpose

Generate an LLM-enhanced ICU evidence dataset for all facilities **offline**, using the
ChatGPT-authenticated Codex CLI instead of the OpenAI API — no `OPENAI_API_KEY`, no
per-token billing (subscription usage limits apply instead). This is a **preprocessing
workflow**: the Streamlit app reads precomputed, verified results and never calls Codex
during normal use. Nothing here replaces the OpenAI API extractor
(`scripts/run_llm_extraction.py`) — the two share the same guardrail code and are
compared against each other.

## Architecture (offline-only)

```
facilities_scored.parquet
    → batch builder selects unprocessed records (checkpoint-aware)
    → codex exec (read-only, ephemeral) receives ONLY evidence-bearing fields on stdin
    → strict schema-constrained JSON result (config/codex_icu_output_schema.json)
    → Pydantic validation + exactly-once ID accounting
    → verbatim fragment anchoring, low-information filter, bed-count anchoring,
      subtype detection  (payload_to_evidence — the SAME code as the OpenAI extractor)
    → existing deterministic validators + evidence/completeness scoring + classification
    → incremental checkpoint (completed.jsonl, per batch)
    → facilities_scored_codex.parquet + codex_comparison.json
```

Codex proposes; it never decides a facility or region classification. Codex runs with
`--sandbox read-only --ephemeral --skip-git-repo-check --cd <run-dir>`: it cannot edit
files, write checkpoints, or execute project code. The Python orchestrator owns all IO.

## Installation & authentication

- Codex CLI (verified against `codex-cli 0.144.6`); check flags with `codex exec --help`.
- Authenticate once with `codex login` (ChatGPT subscription). The pipeline reads no
  `.env` and requires no API key.

Confirmed flags used: `--ephemeral`, `--skip-git-repo-check`, `--sandbox read-only`,
`--cd`, `-m`, `-c model_reasoning_effort=…`, `--output-schema`, `--output-last-message`,
prompt as argument with the batch JSON piped to stdin (Codex appends it as a `<stdin>`
block).

## Model choice

| Model | Role |
|---|---|
| `gpt-5.6-luna` | **default** — fast/affordable; right for 10k repetitive extractions |
| `gpt-5.6-terra` | fallback / quality comparison on the reviewed 24-record sample |
| `gpt-5.6-sol` | frontier agentic model — NOT for bulk extraction |
| `gpt-5.5` | general frontier — not used here |

Default reasoning effort: `low`. Both are CLI-overridable; the actual model/effort are
recorded per record and in the run manifest. Escalate to Terra only if Luna produces
noticeably weaker evidence or malformed outputs on the reviewed sample.

## Commands

```bash
# 24-record pilot
python scripts/run_codex_extraction.py --limit 24 --batch-size 6 \
    --model gpt-5.6-luna --reasoning-effort low

# 100-record pilot
python scripts/run_codex_extraction.py --limit 100 --batch-size 25 \
    --model gpt-5.6-luna --reasoning-effort low

# 500-record stability run
python scripts/run_codex_extraction.py --limit 500 --batch-size 25 \
    --model gpt-5.6-luna --reasoning-effort low

# Full run (~404 codex executions at batch 25) - requires --yes; DO NOT run casually
python scripts/run_codex_extraction.py --limit 10077 --batch-size 25 \
    --model gpt-5.6-luna --reasoning-effort low --yes

# Resume after interruption / usage-limit pause
python scripts/run_codex_extraction.py --resume --model gpt-5.6-luna

# Terra comparison on the reviewed sample (separate output dir)
python scripts/run_codex_extraction.py --limit 24 --batch-size 6 \
    --model gpt-5.6-terra --reasoning-effort low \
    --output-dir data/processed/codex_extraction_terra

# Plan without any Codex call
python scripts/run_codex_extraction.py --limit 24 --dry-run
```

More than 500 selected records refuse to run without `--yes`.

## Checkpoint layout (git-ignored under data/processed/)

```
data/processed/codex_extraction/
├── manifest.json          # run id, model, prompt/schema versions, source hash,
│                          # counts, batch ids, CLI version, repo commit, timings
├── completed.jsonl        # one line per verified+scored record (append+fsync)
├── errors.jsonl           # quarantined records with error reasons
├── batches/
│   ├── batch_000001.input.json    # exact model input (minimal fields)
│   ├── batch_000001.meta.json     # ids, hashes, provenance
│   └── batch_000001.output.json   # raw model result for debugging
├── facilities_scored_codex.parquet
└── codex_comparison.json
```

## Resume & retry behaviour

- `--resume` verifies model, prompt version, schema version and the source-file hash
  against the manifest; mismatches refuse unless `--force-resume`. Completed IDs are
  never rerun; a failed retry never overwrites a successful result.
- `--restart` archives the previous run directory (never deletes).
- Failure ladder per batch: retry once → split in half and recurse → a persistently
  failing single record is quarantined to `errors.jsonl` and the run continues.
- Authentication / usage-limit errors pause the run with an actionable message;
  everything already completed is preserved and `--resume` continues.
- All writes are atomic (tmp+rename) or append+fsync; torn JSONL lines from an
  interrupted write are skipped on resume, not fatal.

## Validation & guardrails (all reused, none duplicated)

Every batch: JSON parse → strict schema → Pydantic → every requested ID exactly once
(missing/duplicate/unknown ⇒ batch failure). Every fragment: verbatim anchoring to the
declared source field (dropped + counted otherwise), low-information single-token
rejection, ICU bed-count accepted only when number+bed+ICU co-occur in ONE verified
fragment, subtype detection over verified fragments only (model-proposed subtypes are
stored as provenance, never used for scoring), specialty tags never count as explicit
claims. Then the standard deterministic validators, scores and classification.

## Limitations & usage notes

- Agreement metrics are diagnostic — the deterministic baseline is not ground truth.
- Subscription usage limits can pause long runs; `--resume` continues them.
- A full 10,077-record run ≈ 404 Codex executions (batch 25) and has NOT been executed.
- Sequential execution; a full run will take hours (measure with the 500-record run).
- Windows: the orchestrator resolves the npm `codex.cmd` shim automatically; run from
  PowerShell if you script around it yourself.

## How to describe this in the demo

> The facility dataset was preprocessed offline with Codex using schema-constrained
> batch extraction. Every proposed quotation was independently anchored to the original
> facility record before deterministic validation and scoring. The live application
> reads precomputed verified results and does not call Codex during normal use.
