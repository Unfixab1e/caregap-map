# Human-review evaluation set

Agreement between the two extractors is **diagnostic, not accuracy**. This folder holds
the workflow for a small human-labelled ground-truth sample.

## Files

- `icu_review_template.csv` — committed schema + two synthetic example rows.
- `private/icu_review.csv` — the real review file (**git-ignored**: it contains real
  facility IDs and source excerpts whose redistribution rights are unclear).

## Workflow

```bash
# 1. Generate the stratified sample (needs processed data):
python scripts/build_eval_sample.py          # -> evals/private/icu_review.csv

# 2. Label it (Nayun): open the CSV, fill the human_* and reviewer columns.
#    Leave a row's human_expected_classification empty to skip it.

# 3. Score the labels:
python scripts/evaluate_labels.py            # -> reports/label_eval_report.json
```

## Sample composition (built by the generator)

- 15 currently Trusted, 10 Needs Human Review, 10 Likely Medical Gap, 5 Insufficient Data
- plus every record where the deterministic and LLM classifications disagree
- plus specialised-subtype records (NICU/PICU/ICCU/...) for subtype diversity

## How to label

| Column | Meaning |
|---|---|
| `human_expected_classification` | one of the four facility classes, judged from the excerpts |
| `explicit_icu_claim` | yes / no / unclear — does the text explicitly claim intensive care? |
| `corroborated` | yes / no — independent support (equipment, beds, staffing, procedures)? |
| `subtype` | e.g. `neonatal_icu` if the claim is a specialised unit only |
| `judgeable` | yes / no — is there enough information to judge at all? |
| `false_trusted_risk` | yes / no — would trusting this record be risky? |
| `false_gap_risk` | yes / no — would calling this a gap be wrong? |
| `reviewer_rationale` | one sentence; quote the deciding phrase if possible |

Judge only what the record *says* — this is dataset consistency, not clinical truth.
False Trusted and false Gap matter more than aggregate accuracy.
