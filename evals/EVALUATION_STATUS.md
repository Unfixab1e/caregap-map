# Human-label evaluation status

Aggregate metrics only — record identifiers and source excerpts stay in
git-ignored files (`evals/private/`, `reports/`).

## Where evaluation stands (2026-07-19)

| item | status |
|---|---|
| Nayun's manual ICU review | 18 cases labelled (see `NAYUN_REVIEW_SUMMARY.md`); the full labelled file is stored privately off-repo and is **not on this machine** |
| Stratified review sample (`evals/private/icu_review.csv`) | 65 rows generated, **0 labelled locally** |
| `scripts/evaluate_labels.py` | runs green; reports "pending" until labels are filled |
| Deterministic-vs-human agreement | **not yet measurable locally** (no labels present) |
| OpenAI-assisted-vs-human agreement | not yet measurable |
| Codex-assisted-vs-human agreement | not yet measurable (full batch run still in progress) |

Until the labelled file is available, **no threshold or classification
change is validated by ground truth** — which is why D21 chose wording
clarification over reclassification.

## Sample composition (65 rows)

By current deterministic classification:

| classification (stored) | rows |
|---|---|
| Likely Medical Gap | 29 |
| Needs Human Review | 16 |
| Trusted ICU Coverage | 15 |
| Insufficient Data | 5 |

The 2026-07-19 expansion appended 22 rows covering the strata the audit
flagged: hospital-like (7), individual doctors (5), diagnostics/labs (4),
dentists (3), pharmacies (2), clinic/health centre (1) among the gap
bucket, plus up-to-3 records per specialised subtype (NICU/PICU/ICCU/
MICU/SICU) and every deterministic-vs-LLM disagreement. Existing rows and
any labels are never overwritten (the generator is merge-preserving).

## Diagnostic (NOT accuracy) model-to-model numbers

Model-to-model agreement says nothing about correctness; it only flags
records worth a human look:

- OpenAI extractor vs deterministic: 75.0 % classification agreement on
  the 24-record stratified comparison (0 API errors).
- Codex (gpt-5.6-luna) pilot vs OpenAI extractor: 87 %; vs
  deterministic: 74 % (24-record pilot).

Every disagreement is auto-included in the review sample.

## Priorities when labelling

1. **False Trusted** (a wrong Trusted hides a real gap) — worst failure.
2. **False facility-level gap** (manufactures deserts), especially on
   non-hospital records: the sample's `audit_category` column lets
   `evaluate_labels.py` report per-category error rates.
3. Subtype confusions (NICU/PICU/ICCU treated as general adult ICU).

## How to complete

```bash
# fill human_* columns in evals/private/icu_review.csv, then:
python scripts/evaluate_labels.py   # -> reports/label_eval_report.json
```

The report now includes `codex_assisted` agreement when a
`codex_classification` column is present, and `by_audit_category` error
breakdowns for non-hospital semantics.
