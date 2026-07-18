# CareGap Map 🏥

**A trust layer for ICU coverage planning in India.**
Databricks Data Legend challenge — Medical Desert Planner mission.

CareGap Map helps NGO and public-health planners tell a **likely real ICU gap** apart from a
gap caused by **incomplete data**. The system never treats *"no reliable ICU evidence"* as
*"no ICU exists"* — facilities and regions land in one of four states:

| | State | Meaning |
|---|---|---|
| 🟢 | Trusted ICU Coverage | strong evidence, sufficient data |
| 🔴 | Likely Medical Gap | well-documented record, no ICU evidence |
| ⚪ | Insufficient Data / Data Desert | cannot be judged — *unknown*, not a gap |
| 🟡 | Needs Human Review | contradictory, suspicious or ambiguous evidence |

Every classification is traceable to the **exact original text fragments** that produced it.
See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the frozen scope and
[DECISIONS.md](DECISIONS.md) for design decisions.

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
2. Read the regional verdict: trust-weighted coverage, judgeable-record share, and the
   four-state breakdown — **medical gaps and data deserts are never conflated**.
3. Open the facility table behind the regional result, filter by classification.
4. Drill into a facility: original record, exact evidence fragments, score breakdown,
   validator flags, missing evidence.
5. Save reviewer notes on a facility, district or state (stored in `data/reviews.db`).

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
  data_access.py           DataSource protocol (local now, Databricks later)
  persistence.py           ReviewStore protocol (SQLite implementation)
scripts/
  profile_data.py          raw-data validation & profiling report
  build_processed_data.py  reproducible cleaning + scoring pipeline
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

## Roadmap

- Execute the Databricks deployment against a live workspace (steps ready, needs credentials).
- Run the LLM comparison at scale and tune extraction prompts on disagreements.
- NFHS district-level join with recorded match confidence.

See [TASKS.md](TASKS.md) for the current status.
