# Submission summary — CareGap Map

**Challenge:** Databricks Data Legend — Building the Trust Layer for Indian Healthcare
**Mission:** Medical Desert Planner (ICU)

| | |
|---|---|
| Live app | https://caregap-map-7474654537485030.aws.databricksapps.com (workspace login required) |
| Repository | https://github.com/Unfixab1e/caregap-map |
| Workspace | https://dbc-3fe4db90-7a41.cloud.databricks.com |
| MLflow evaluation | experiment `/Users/blubthefish@gmail.com/caregap-evaluation` → Traces tab |

## One paragraph

CareGap Map is a trust layer over 10,077 supplied facility records: a
deterministic, fully traceable evidence pipeline classifies every record
(Trusted ICU evidence / Needs Human Review / No ICU evidence in judgeable
record / Insufficient Data), a regional layer separates **potential
planning gaps** from **data deserts**, and a planner workflow (drilldown
with exact fragments, planning-readiness checklist, durable notes and
saved planning scenarios) runs as a Databricks App over Unity Catalog
storage with Delta persistence and MLflow 3 evaluation.
**Databricks is the governed operating layer; CareGap Map is the trust
and planning logic built on top of it.**

## Demo video checklist (90 s — see DEMO_SCRIPT.md)

- [ ] Headline: 203 of 10,077 ≠ "only 203 ICUs"
- [ ] Data desert vs potential planning gap (two districts)
- [ ] Drilldown: fragment, categories, subtype, validator flag, missing
      evidence, planning readiness
- [ ] Save + reopen a planning scenario (refresh survives)
- [ ] Databricks architecture beat (App / volume / warehouse / Delta / MLflow)
- [ ] Close: "only source-verifiable evidence affects the score"

## Tech video checklist

- [ ] Architecture diagram (README) — Databricks vs custom logic
- [ ] `scripts/audit_headline_metrics.py` output — the published self-audit
- [ ] MLflow: experiment `caregap-evaluation` → Traces tab → open one
      `facility_<id>` trace → show the 11 pipeline spans; run
      `final-candidate-eval` metrics + `evaluation_summary.json` artifact
- [ ] Delta tables: `workspace.caregap.review_notes`,
      `workspace.caregap.planning_scenarios` (parameterized SQL only)
- [ ] CI green without any credentials or challenge data

## Dataset & licensing statement

The challenge datasets (facilities, PIN directory, NFHS-5) are inputs
with unclear redistribution rights: no raw or processed record content,
identifiers or excerpts are committed to the repository — only code,
documentation, synthetic samples and aggregate metrics. Local outputs
containing real content stay git-ignored (`data/`, `reports/`,
`evals/private/`). The upstream dataset fields are themselves
model-generated claims; the app says so and never certifies real-world
clinical availability. Secrets are never committed; the deployed app uses
the Databricks-injected service-principal OAuth, not tokens.

## Known limitations (details in README)

- Signals are dataset consistency, not verified clinical capability.
- "Judgeable" = populated fields; most gap-bucket records are
  non-hospital organizations (published audit).
- Human labels pending locally: no threshold change is ground-truth
  validated yet; model-to-model agreement is diagnostic only.
- 64 of 103 trusted-evidence districts hinge on a single record.
- NFHS is cleaned but not joined; choropleth geometry out of scope.
