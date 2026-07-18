# Task tracking

Legend: ✅ code-complete **and** locally tested · 🟡 code-complete, **not live-tested** ·
⬜ incomplete · ✨ stretch

## Milestone 1 — deterministic local vertical slice ✅ (2026-07-18)

- ✅ Repo protection, docs, tooling, profiling, cleaning pipeline
- ✅ Deterministic evidence extraction with exact fragments; validators
- ✅ Independent evidence/completeness scores + four-state classification
- ✅ State/district aggregation separating gaps from data deserts
- ✅ Streamlit UI with facility drilldown + reviewer notes (SQLite)

## Milestone 2 — Databricks deployment

- ✅ `DatabricksDataSource` (SQL warehouse, stub-tested) + `get_data_source()` factory
- ✅ requirements.txt for the Databricks Apps runtime (Path A)
- ✅ DEPLOYMENT.md incl. durable-notes acceptance test; scripts/register_tables.sql
- ✅ Databricks CLI v1.8.0 installed on the dev machine
- ⬜ **Live deployment — BLOCKED on workspace credentials** (`databricks auth login`
  needs an interactive browser session by a workspace member). All artifacts ready.
- ⬜ Live workflow test in a browser (All-India → state → district → drilldown → note)

## Milestone 3 — LLM evidence extractor

- ✅ `LlmEvidenceExtractor` (same interface/model), verified source-anchored quotes
- ✅ Real-API comparison runs (gpt-4o-mini, 24 stratified records, 0 errors, ≈$0.005/run)
- ✅ Manual review of every disagreement → reports/llm_disagreement_review.md (ignored)
- ✅ Guardrails hardened from the review: low-information filter, per-group quotes,
  bed-count anchoring (D15)

## Trust-layer hardening (2026-07-18)

- ✅ ICU subtype extraction + UI wording (D16)
- ✅ Trusted calibration: explicit claim + ≥2 independent corroboration categories
  (D14; Trusted 2,006 → 535, all demotions to review, none to gap)
- ✅ Regional wording: evidence ≠ coverage; non-scope disclaimer (Phase 8)
- ✅ Durable notes: `DeltaReviewStore` + `CAREGAP_REVIEW_STORE` factory (stub-tested)
- 🟡 Durable-notes refresh/redeploy acceptance — needs the live workspace
- ✅ Human-review evaluation workflow (evals/ + generator + evaluator, 45-row sample
  generated locally)
- ⬜ Human labels (Nayun) → then re-calibrate thresholds against ground truth
- ✅ GitHub Actions CI (push/PR; data-free test suite) — 🟡 green run pending push
- ✅ Facility evidence point map (beta, honest wording, table fallback)
- ✨ District choropleth (needs district geometry with recorded match confidence)
- ✨ MLflow tracing (deferred, D17 — UI already exposes the full audit chain)

## Backlog

- ⬜ NFHS district-level join (fuzzy match with recorded confidence)
- ⬜ Reviewer-note export (CSV) for planning workflows
- ⬜ Path B service-principal OAuth (avoid PAT in app config; use app resources)
