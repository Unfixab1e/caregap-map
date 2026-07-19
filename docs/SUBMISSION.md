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
record / Insufficient Data) under versioned evidence rules (Policy v2: a
bare ICU keyword is never trusted; a substantive description statement
plus one operational signal can be), a regional layer separates
**potential planning gaps** from **data deserts**, and a planner workflow (drilldown
with exact fragments, operational-data checklist, durable notes and
saved planning scenarios) runs as a Databricks App over Unity Catalog
storage with Delta persistence and MLflow 3 evaluation.
**Databricks is the governed operating layer; CareGap Map is the trust
and planning logic built on top of it.**

## 20–30 s visual sequence (intro video)

1. Land on the India evidence map; the sidebar CareGap workflow sits at Step 1.
   Open a district (or click an "Explore an example" shortcut).
2. The hero card answers immediately: status, data-vs-evidence meaning,
   recommended action, and the "Why this status?" path.
3. Click one priority facility → decision summary, exact source fragment,
   missing/suspicious evidence.
4. End on "Save this planning scenario" - the sidebar workflow ticks Step 4
   complete on save.

## Demo video checklist (90 s — see DEMO_SCRIPT.md)

- [ ] Headline: 275 of 10,077 ≠ "only 275 ICUs" (Evidence Policy v2)
- [ ] Data desert vs potential planning gap (two districts)
- [ ] Drilldown: fragment, categories, subtype, validator flag, missing
      evidence, operational data availability
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
- [ ] Evaluation honesty lines:
      “The challenge provides no ground-truth answer key, so we do not claim
      clinical accuracy. We evaluate the trust pipeline through deterministic
      validators, exact-fragment verification, adversarial audit samples and
      disagreement analysis across independent extractors.”
      “Model agreement is diagnostic, not accuracy. We intentionally traced
      difficult disagreement cases to expose where a human reviewer is needed.”

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

## Evaluation status

- ✅ Deterministic regression tests
- ✅ Exact-fragment verification
- ✅ Validator and contradiction tests
- ✅ OpenAI–deterministic comparison
- ✅ Codex–OpenAI–deterministic disagreement analysis
- ✅ Bounded MLflow traced evaluation
- ⬜ Structured human-labelled accuracy benchmark

Human-labelled calibration remains **future work**; no clinical accuracy
claim is made. The pipeline is designed for human review and future
calibration — structured labelling will not complete before submission
and release does not block on it. Model agreement is diagnostic, not
accuracy.

## Known limitations (details in README)

- Signals are dataset consistency, not verified clinical capability.
- "Judgeable" = populated fields; most gap-bucket records are
  non-hospital organizations (published audit).
- The structured human-labelled accuracy benchmark is incomplete and will
  not complete before submission; human-labelled calibration remains
  future work, no threshold change is ground-truth validated, and no
  clinical accuracy claim is made. Extractor-agreement numbers are
  diagnostic and come from small or deliberately disagreement-heavy
  samples.
- 77 of 132 trusted-evidence districts hinge on a single record.
- NFHS is cleaned but not joined; choropleth geometry out of scope.
