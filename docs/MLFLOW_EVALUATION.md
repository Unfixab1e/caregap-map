# MLflow 3 evaluation & tracing (bounded, optional)

An **offline quality-evaluation workflow** for the ICU evidence pipeline —
not a live-app dependency. The deployed app never imports MLflow; every
test passes and the deterministic pipeline runs when MLflow, Databricks
credentials or network access are absent.

## What it does

`scripts/run_mlflow_evaluation.py` selects a bounded, representative
facility sample (default ≤ 65 records — never the full 10,077):

- every classification (6 per class by default);
- specialised subtypes (NICU/PICU/ICCU/MICU/SICU, up to 3 each);
- non-hospital audit categories among the gap bucket (up to 3 each);
- every stored OpenAI disagreement and Codex-pilot disagreement;
- every human-labelled record (when `evals/private/icu_review.csv` has
  labels).

For each record it emits **one MLflow trace** re-running the deterministic
chain stage by stage with a span per stage:

1. `load_supplied_record` — populated-field count
2. `deterministic_extraction` — proposed fragments by signal group,
   explicit claim, anchored bed count
3. `exact_fragment_verification` — verified/dropped/low-information
   counters (deterministic fragments are verbatim by construction; the
   drop counters exist for model extractors)
4. `icu_subtype_detection`
5. `validators` — flag names, contradiction/suspicious outcome
6. `evidence_category_calculation` — corroboration categories
7. `evidence_score` / 8. `completeness_score` — values + components
9. `classification` — class + reason
10. `comparison` — stored deterministic / OpenAI / Codex / human labels

One MLflow **run** aggregates: records processed/succeeded/errors/
quarantined, verified-fragment counts, explicit claims, subtype counts,
validation-flag counts, agreement percentages (stored-deterministic
determinism check, OpenAI, Codex, human), false-Trusted / false-Gap vs
human labels, confusion matrices (artifact `evaluation_summary.json`),
latency, token usage and cost (zero — the traced pipeline is
deterministic; model outputs are compared from earlier recorded runs).

**Model-to-model agreement is diagnostic, never accuracy.** Human-label
metrics appear only when labelled rows exist; the run does not claim
judge alignment otherwise.

## Privacy

Traces carry record identifiers, counts, scores, flag/category names and
classification labels only — never full raw records, fragment text, note
content or credentials (covered by a regression test).

## Running it

```bash
pip install -e ".[mlflow]"

# Databricks backend (workspace dbc-3fe4db90-7a41):
export MLFLOW_TRACKING_URI=databricks
export DATABRICKS_HOST=https://dbc-3fe4db90-7a41.cloud.databricks.com
export DATABRICKS_TOKEN=$(databricks auth token -p dbc-3fe4db90-7a41 | jq -r .access_token)

python scripts/run_mlflow_evaluation.py \
    --codex-parquet data/processed/codex_pilot_snapshot.parquet
```

- Experiment: `/Users/blubthefish@gmail.com/caregap-evaluation`
- Traces: Databricks → Machine Learning → Experiments →
  `caregap-evaluation` → **Traces** tab (one trace per facility,
  `facility_<unique_id>`); run metrics under the run, full summary as the
  `evaluation_summary.json` artifact.
- A local copy of the summary lands in `reports/mlflow_eval_summary.json`
  (git-ignored).
- `--codex-parquet` must point at a **stable snapshot** — never the active
  batch-output directory of a running extraction.

Without MLflow installed the script exits with an actionable message and
touches nothing.
