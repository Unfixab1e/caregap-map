# Demo script — the 90-second flow

**Setup:** pipeline built (`python scripts/build_processed_data.py`), app open
(`streamlit run app.py`), a district with *Insufficient data to assess* and a district
with *Potential planning gap* pre-identified in two browser tabs.

**Core line:** *"No ICU evidence and not enough data to know are different planning
situations."*

**Secondary line:** *"The model proposes evidence, but only source-verifiable evidence
is allowed to affect the score."*

## The 90 seconds

1. **(0:00–0:15) Data-desert district.** Open the ⚪ district.
   > "10,000 facility records, but a quarter of the fields are empty. This district has
   > records — they're just too thin to judge. CareGap Map **refuses** to call it an ICU
   > desert: *Insufficient data to assess*. That's a data problem, not a confirmed
   > medical gap."

2. **(0:15–0:30) Planning-gap district.** Switch to the 🔴 district.
   > "Here the records are complete and judgeable — and none of them shows credible ICU
   > evidence. *Potential planning gap.* Same map colour logic, opposite meaning to a
   > data desert — and the tool never conflates the two."

3. **(0:30–1:00) Facility drilldown.** Open one 🟢 trusted (or 🟡 review) facility.
   - point at the **exact source fragment** ("22-bed Level II Intensive Care Unit …")
   - **extractor provenance** (deterministic / LLM — LLM quotes are verified verbatim
     against the source; hallucinated quotes are dropped and flagged)
   - **ICU subtype** ("NICU only — no general adult ICU claim" where applicable)
   - **independent corroboration** count ("a marketing phrase can't corroborate itself")
   - **missing evidence** list and validator flags
   > "Every score is traceable to the sentence that produced it. No black box."

4. **(1:00–1:20) Reviewer note.** On the district, save:
   > *"Verify these facilities before classifying this district as an ICU desert."*
   Refresh the page — the note is still there (SQLite locally, Delta table on
   Databricks, surviving restarts).

5. **(1:20–1:30) Close.**
   > "Deterministic, configurable, human-calibrated — we manually reviewed every
   > LLM disagreement, found our own false-Trusted pattern, and fixed it with a
   > corroboration rule. That's the trust layer."

## Honesty guardrails (never say)

- any verified clinical claim ("this hospital HAS an ICU")
- patient recommendations or referrals
- that geographic access / travel time is measured
- population-adjusted conclusions
- that a green state means adequate coverage (it means *evidence exists*)

## Fallback

If the app fails: `python -m pytest` (142 green tests),
`data/processed/cleaning_summary.json` and `data/processed/llm_comparison.json` tell
the story from the terminal.
