# Task tracking

## Milestone 1 — deterministic local vertical slice ✅ (2026-07-18)

- [x] Protect repo: .gitignore for raw/processed data, env files, local DBs
- [x] Project docs: PROJECT_SPEC, DECISIONS, DEMO_SCRIPT, README, TASKS
- [x] Python tooling: pyproject (pandas, pyarrow, streamlit, plotly, pydantic; pytest, ruff)
- [x] scripts/profile_data.py — raw validation + machine-readable report
- [x] scripts/build_processed_data.py — reproducible Parquet pipeline + cleaning summary
- [x] Deterministic ICU evidence extraction with exact fragments (evidence.py)
- [x] Deterministic validators (validator.py)
- [x] Independent evidence/completeness scoring + four-state classification (scoring.py)
- [x] State/district aggregation separating gaps from data deserts (aggregation.py)
- [x] Streamlit UI: region summary, classification chart, facility table, drilldown, notes
- [x] Persistence interface + SQLite reviewer-note store
- [x] Test suite (69 tests: cleaning, geography, evidence, scoring, aggregation,
      persistence, sample end-to-end, app smoke)

## Milestone 2 — Databricks deployment (next)

- [ ] Upload raw CSVs to a Unity Catalog volume; run pipeline as a Databricks job/notebook
- [ ] `DatabricksDataSource` implementing the existing `DataSource` protocol
      (databricks-sql-connector or Spark)
- [ ] Reviewer notes on Lakebase/Delta via the existing `ReviewStore` protocol
- [ ] Deploy Streamlit app as a Databricks App (app.yaml present)

## Milestone 3 — optional LLM evidence extractor

- [ ] `LlmEvidenceExtractor` implementing the same interface as `extract_evidence`
- [ ] Sentence-level evidence selection + unclear-claim categorisation
- [ ] Deterministic validation stays mandatory on top of LLM output
- [ ] Side-by-side eval: deterministic vs LLM extraction on a labelled sample

## Backlog / known limitations

- [ ] NFHS district-level join (needs fuzzy match with recorded confidence)
- [ ] India map visualisation (choropleth) — table/bar first, map later
- [ ] Reviewer-note export (CSV) for planning workflows
- [ ] `possible_duplicate_facility`: consider address-level similarity, still no auto-merge
