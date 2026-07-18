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
- ✅ **Live deployment (2026-07-18):**
  https://caregap-map-7474654537485030.aws.databricksapps.com — Path A (volume
  Parquet) + Delta notes via app SQL-warehouse resource (SP OAuth, no tokens in
  config); deployment SUCCEEDED, app RUNNING, authenticated HTTP 200 verified
- ✅ Durable-note acceptance at the storage layer: a Delta note written before a full
  app stop/start survived the restart
- ⬜ Human in-browser click-through (state → district → drilldown → save a note via
  the UI, exercising the SP write path end-to-end) — 2 minutes for any teammate

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
- ✅ Durable notes: `DeltaReviewStore` + `CAREGAP_REVIEW_STORE` factory; live-tested
  against `workspace.caregap.review_notes` incl. survival across app restart
- ✅ Human-review evaluation workflow (evals/ + generator + evaluator, 45-row sample
  generated locally)
- ⬜ Human labels (Nayun) → then re-calibrate thresholds against ground truth
- ✅ GitHub Actions CI (push/PR; data-free test suite) — 🟡 green run pending push
- ✅ Facility evidence point map (beta, honest wording, table fallback)
- ✨ District choropleth (needs district geometry with recorded match confidence)
- ✨ MLflow tracing (deferred, D17 — UI already exposes the full audit chain)

## Provenance hardening (2026-07-18, from challenge prompt files — D18)

- ✅ Specialty tags (criticalCareMedicine) demoted from explicit claim to context signal
- ✅ Cross-field agreement removed from Trusted corroboration (fields generated in one
  upstream pass); component renamed cross_field_consistency
- ✅ directory_or_partner_content_detected validator (multi-facility page leakage)
- ✅ UI/docs layer-corrected: "supplied record", never "original website sentence"
- ✅ Before/after: Trusted 535 → 203, Review 2,535 → 2,867, Gap/Insufficient unchanged
- ⬜ Relax/tune with human labels once evals/private/icu_review.csv is filled

## Offline Codex extraction pipeline (2026-07-18)

- ✅ Resumable `codex exec` batch pipeline (ChatGPT-authenticated, no OPENAI_API_KEY);
  shared payload_to_evidence guardrails; deterministic scoring reused; checkpoints,
  retry→split→quarantine, atomic writes; 31 stub-backed tests
- ✅ Real 24-record Luna pilot: 23 ok / 1 quarantined (the corrupted column-shifted
  record - guardrails, not model weakness), 87% agreement with the OpenAI extractor,
  74% with deterministic, ~26 s/record; Terra escalation judged unnecessary
- ⬜ 100- and 500-record stability runs, then decide on the full 10,077 sweep
  (~404+ executions; NOT executed)

## Backlog

- ⬜ NFHS district-level join (fuzzy match with recorded confidence)
- ⬜ Reviewer-note export (CSV) for planning workflows
- ⬜ Path B service-principal OAuth (avoid PAT in app config; use app resources)
