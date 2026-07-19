# CareGap Map 🏥

**A trust layer for ICU coverage planning in India.**
Databricks Data Legend challenge — Medical Desert Planner mission.

**Problem:** public facility data is messy — 10,088 records, 253 spellings of state
names, capacity filled for a quarter of facilities, and "ICU" in a marketing blurb is not
a verified ICU. Mapped naively, every badly documented district looks like a medical
desert.

**Core differentiator:** CareGap Map never treats *"no reliable ICU evidence"* as
*"no ICU exists."* Facilities land in one of four states (stored constant → displayed
wording, D19):

| | Displayed as | Stored constant | Meaning |
|---|---|---|---|
| 🟢 | Trusted ICU evidence | `Trusted ICU Coverage` | explicit claim + ≥2 distinct evidence categories in the record |
| 🔴 | No ICU evidence in judgeable record | `Likely Medical Gap` | well-populated record, no credible ICU evidence found |
| ⚪ | Insufficient Data | `Insufficient Data` | cannot be judged — *unknown*, not a gap |
| 🟡 | Needs Human Review | `Needs Human Review` | contradictory, suspicious, ambiguous or uncorroborated |

Regions use deliberately different wording — **"Trusted ICU evidence found"**,
**"Potential planning gap"**, **"Insufficient data to assess"**, **"Needs facility
verification"** — because evidence presence is not coverage sufficiency, and because the
facility level states record evidence while only the regional layer draws planning
conclusions. ICU subtypes (NICU/PICU/ICCU/…) are surfaced and never displayed as
confirmed general adult ICU.

Every classification is traceable to **exact text fragments from the supplied facility
record**, for both the deterministic extractor and the optional LLM extractor (whose
quotes are verified verbatim against the record; hallucinated quotes are dropped and
flagged). See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the frozen scope and
[DECISIONS.md](DECISIONS.md) for the decision log (D1–D22).

## How to read the headline numbers

Current All-India values and what they do — and do not — mean:

- **Trusted ICU evidence: 203 of 10,077 records.** Only 203 supplied records meet the
  strict Trusted evidence standard. **That is not the same as saying only 203 facilities
  have an ICU** — 2,867 records carry unverified or uncorroborated claims awaiting
  review.
- **No ICU evidence in judgeable record: 6,890.** The audit
  (`scripts/audit_headline_metrics.py`) shows only ~36 % of these are even hospital-like
  by name; ~35 % are clearly non-hospital organizations (1,713 dental practices, 588
  diagnostics/labs, 75 individual doctors, 2 pharmacies). A judgeable pharmacy record
  without ICU evidence is an expected absence, not a medical gap — which is why the
  facility label states evidence absence and only the regional layer says "potential
  planning gap".
- **Judgeable records: 99 %.** This means the records' *fields are populated* enough to
  evaluate what each record claims (record judgeability). It does **not** mean the
  content is ICU-informative (73.7 % of judgeable records carry only generic non-ICU
  procedure/equipment content) and it is **not** planning readiness (74.8 % lack
  capacity, 63.6 % lack a doctor count) — see the per-facility planning-readiness
  checklist (D20).
- **Trust-weighted ICU evidence index (0–1)** — average capability-evidence score
  weighted by record completeness. **Not** population or geographic coverage.
- **Trusted-record share (2 %)** — share of supplied records classified Trusted under
  the current rules. **Not** "2 % national ICU coverage".

Reproduce the full audit behind these statements with
`python scripts/audit_headline_metrics.py` (writes git-ignored
`reports/headline_metric_audit.{json,md}`).

## Dataset-generation provenance (what "evidence" means here)

The supplied facility fields (`description`, `capability`, `procedure`, `equipment`,
`specialties`) are **structured claims generated upstream** from source website content
using extraction prompts — `capability`/`procedure`/`equipment` were produced together in
**one extraction pass** over text **and images**, and specialty tags can derive from the
facility *name* alone. These claims are not independently verified clinical facts, and a
claim may have no original webpage sentence at all (image-derived). CareGap Map therefore
validates **internal consistency and supplied-record traceability** — cross-field
agreement is treated as consistency, never as independent confirmation — and does not
certify live service availability (see DECISIONS D18).

## Quickstart

Requires Python ≥ 3.11.

```bash
# 1. Install (editable, with dev tools)
pip install -e ".[dev]"

# 2. Put the raw challenge files in place (never committed):
#    data/raw/facilities.csv
#    data/raw/india_post_pincode_directory.csv
#    data/raw/nfhs_5_district_health_indicators.csv

# 3. Validate & profile the raw data (writes reports/profile_report.json)
python scripts/profile_data.py

# 4. Build processed Parquet outputs (writes data/processed/*)
python scripts/build_processed_data.py

# 5. Run the tests
python -m pytest

# 6. Launch the app
streamlit run app.py
```

`pip install -e .` is optional for running the scripts and app — both bootstrap
`src/` onto `sys.path` — but recommended for development.

**Two installation surfaces, deliberately separate:**

| Context | Install with | Contents |
|---|---|---|
| Local development | `pip install -e ".[dev]"` | package + pytest + ruff |
| Offline LLM comparison | `pip install -e ".[llm]"` | + openai |
| Path B (UC tables) local test | `pip install -e ".[databricks]"` | + databricks-sql-connector |
| **Databricks App runtime** | automatic, from [requirements.txt](requirements.txt) | streamlit, pandas, pyarrow, plotly, pydantic only |

Databricks Apps install from `requirements.txt`, not `pyproject.toml`. The app runtime
deliberately excludes `openai` (LLM extraction is an offline preprocessing workflow) and
`databricks-sql-connector` (only needed if the deployed app switches to Path B).

## What the app does

1. Capability is fixed to **ICU**; pick a state and optionally a district.
2. Read the regional verdict: the trust-weighted ICU evidence index, judgeable-record
   share, trusted-record share and the four-state breakdown — **evidence gaps and data
   deserts are never conflated**.
3. Open the facility table behind the regional result, filter by evidence status.
4. Drill into a facility: supplied record, exact evidence fragments, score breakdown,
   validator flags, missing evidence, and the **planning-readiness checklist** (three
   separate concepts: record judgeability, ICU evidence strength, planning readiness —
   D20).
5. Save **planning scenarios** (structured snapshot of the selection + aggregate metrics
   + note; reopen/delete later — D22) and reviewer notes on a facility, district or
   state. Both persist in SQLite locally and Delta tables on Databricks.

> All signals reflect **dataset consistency, not verified clinical capability**.
> This tool makes no medical claims.

## Project layout

```
app.py                     Streamlit UI (presentation only)
app.yaml                   Databricks Apps launch config
src/caregap_map/
  config.py                ALL keywords, weights, thresholds, paths
  cleaning.py              null-like handling, parsing, state/PIN normalisation
  geography.py             PIN-directory aggregation + geo assignment
  evidence.py              deterministic ICU evidence extraction (fragments!)
  validator.py             dataset-consistency checks
  scoring.py               independent evidence & completeness scores + classes
  aggregation.py           state/district rollups, gap-vs-desert logic
  audit.py                 headline-metric diagnostics + audit categorizer
  planning.py              planning-readiness checklist (separate from judgeability)
  scenarios.py             PlanningScenario model + SQLite/Delta stores
  data_access.py           DataSource protocol (local now, Databricks later)
  persistence.py           ReviewStore protocol (SQLite implementation)
scripts/
  profile_data.py          raw-data validation & profiling report
  build_processed_data.py  reproducible cleaning + scoring pipeline
  audit_headline_metrics.py  reproducible audit of the displayed numbers
tests/                     unit + app smoke tests (synthetic fixtures)
data/raw|processed         git-ignored challenge data
data/samples               tiny synthetic sample (committed, no real data)
```

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `CAREGAP_DATA_DIR` | root of `raw/` and `processed/` | `data` |
| `CAREGAP_SCORING_CONFIG` | JSON overriding any scoring weight/threshold | built-ins |

See [.env.example](.env.example). Thresholds are documented in
[DECISIONS.md](DECISIONS.md) (D4, D5, D7).

## Databricks deployment

Two paths, both documented step-by-step in [DEPLOYMENT.md](DEPLOYMENT.md):

- **Path A:** the app reads processed Parquet from a mounted Unity Catalog volume
  (`CAREGAP_DATA_DIR=/Volumes/...`) — zero code changes.
- **Path B:** `CAREGAP_DATA_SOURCE=databricks` reads registered UC tables through a SQL
  warehouse via the `DatabricksDataSource` adapter
  (`pip install -e ".[databricks]"`, tables via [scripts/register_tables.sql](scripts/register_tables.sql)).

## Optional LLM evidence extractor

`LlmEvidenceExtractor` ([src/caregap_map/llm_extraction.py](src/caregap_map/llm_extraction.py))
implements the same interface as the deterministic extractor. Guardrails:

- every quoted fragment must be **verified as an exact substring** of the source record —
  hallucinated quotes are dropped and flagged;
- an ICU claim or bed count only counts when backed by a verified fragment;
- scoring, validation and classification remain **fully deterministic** for both extractors.

Compare it against the baseline on a stratified sample (`pip install -e ".[llm]"`,
key in `.env` as `OPENAI_API_KEY=...` — the scripts load `.env` automatically):

```bash
python scripts/run_llm_extraction.py --limit 24
```

Outputs `data/processed/llm_comparison.json` (agreement metrics, measured token usage,
estimated cost) and `facilities_scored_llm.parquet`. The app continues to display the
deterministic results.

**Cost guardrails** (for the challenge's limited API credit): the script prints an
estimated cost before calling the API, refuses runs estimated above **$2** unless you
pass `--yes`, reports live spend per record, and gpt-4o-mini keeps even a full 10k-record
run in the low single-digit dollars. Prices used for the estimate are configurable in
`LlmConfig`.

## Evaluation against human labels

Extractor agreement is diagnostic, not accuracy. `evals/` holds a labelled-review
workflow (template committed; real excerpts stay git-ignored) — current status in
[evals/EVALUATION_STATUS.md](evals/EVALUATION_STATUS.md):

```bash
python scripts/build_eval_sample.py   # merge-preserving stratified sample (65 rows:
                                      # classes, subtypes, audit categories, model
                                      # disagreements; --codex-parquet for a STABLE
                                      # Codex snapshot)
# label evals/private/icu_review.csv, then:
python scripts/evaluate_labels.py     # false-Trusted / false-Gap first-class metrics,
                                      # plus codex_assisted + per-audit-category errors
```

## Live app & deployment status

**Live** on Databricks Apps (workspace `dbc-3fe4db90-7a41`):

> **https://caregap-map-7474654537485030.aws.databricksapps.com**
> (requires workspace login; Path A — Parquet from the `workspace.caregap.caregap_data`
> volume; reviewer notes in the `workspace.caregap.review_notes` Delta table via the
> app's SQL-warehouse resource with service-principal OAuth)

Verified programmatically: deployment probe SUCCEEDED, app RUNNING, authenticated
HTTP 200 on the URL before and after a full stop/start cycle, and a reviewer note
written to the Delta table before the restart was still present after it. See
[DEPLOYMENT.md](DEPLOYMENT.md) for the exact executed steps.

## Limitations (honest list)

- Signals reflect **dataset consistency, not verified clinical capability**; no medical
  claims, referrals, or diagnosis.
- Regional statuses describe **evidence**, not population need, bed availability, travel
  time, or physical accessibility.
- "Judgeable" measures populated fields, not ICU-informative content: 86.6 % of
  judgeable records pass solely on upstream-generated text fields, and most contain no
  ICU-relevant procedure/equipment mention at all (see the headline-metric audit).
- The facility-level "No ICU evidence" bucket is dominated by organizations that may
  never be expected to run an ICU (dental practices, labs, small clinics); the audit
  categorizer that quantifies this is name-based and conservative — audit reporting
  only, never clinical truth.
- LLM extraction errs toward *missing* evidence (exact-quote discipline) — the safe
  direction, but a recall limitation.
- NFHS indicators are cleaned but not yet joined at district level.
- The expanded human-labelled evaluation sample (65 rows) exists but local labels are
  pending (Nayun's 18-case review file is stored privately off-repo); no threshold
  change is currently ground-truth-validated, which is why D21 clarifies wording
  instead of reclassifying.
- 64 of 103 trusted-evidence districts hinge on a single trusted record; 88 districts
  are dominated by non-hospital records.

## Privacy & licensing

Raw challenge CSVs, processed outputs, LLM comparison outputs, evaluation excerpts,
local databases and `.env` are all git-ignored — the repository contains only code,
docs, and a small synthetic sample. Redistribution rights of the source records are
unclear, so no real record content is committed.

See [TASKS.md](TASKS.md) for code-complete vs live-tested status.
