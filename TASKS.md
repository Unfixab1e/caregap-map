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

## Milestone 2 — Databricks deployment ✅ code-complete (2026-07-18)

- [x] `DatabricksDataSource` implementing the existing `DataSource` protocol
      (databricks-sql-connector, unit-tested via injected connection)
- [x] `get_data_source()` factory: `CAREGAP_DATA_SOURCE=local|databricks`; app wired to it
- [x] DEPLOYMENT.md: volume upload, app create/deploy, both data paths, pipeline-as-job
- [x] scripts/register_tables.sql: UC table registration + service-principal grants
- [ ] Execute against a live workspace (blocked: no workspace credentials on dev machine)
- [ ] Reviewer notes on Lakebase/Delta via the existing `ReviewStore` protocol

## Milestone 3 — optional LLM evidence extractor ✅ code-complete (2026-07-18)

- [x] `LlmEvidenceExtractor` implementing the same interface as `extract_evidence`
      (same `EvidenceResult` model, provenance recorded)
- [x] Sentence-level evidence selection with **verified source-anchored quotes**;
      hallucinated fragments dropped + flagged; unclear-claim categorisation + explanation
- [x] Deterministic validation, scoring and classification stay mandatory on LLM output
- [x] Side-by-side eval script: scripts/run_llm_extraction.py (stratified sample,
      agreement metrics)
- [x] Comparison run against the real OpenAI API (2026-07-18): 24 stratified records,
      0 errors, 75% classification agreement, ~$0.005 measured cost; two guardrail
      improvements came out of the disagreement review (low-information fragment
      filter, multi-group quotes)

## Backlog / known limitations

- [ ] NFHS district-level join (needs fuzzy match with recorded confidence)
- [ ] India map visualisation (choropleth) — table/bar first, map later
- [ ] Reviewer-note export (CSV) for planning workflows
- [ ] `possible_duplicate_facility`: consider address-level similarity, still no auto-merge
