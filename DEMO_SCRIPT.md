# Demo script — the 90-second flow

**Setup:** pipeline built (`python scripts/build_processed_data.py`), app open
(`streamlit run app.py`), a district with *Insufficient data to assess* and a district
with *Potential planning gap* pre-identified in two browser tabs.

**Core line:** *"No ICU evidence and not enough data to know are different planning
situations."*

**Secondary line:** *"Only 203 of 10,077 supplied records meet our strict Trusted ICU
evidence standard. That is **not** the same as saying only 203 facilities have an ICU."*

## The 90 seconds

1. **(0:00–0:15) Headline honesty.** All-India view.
   > "10,077 supplied records. Only 203 meet our strict Trusted ICU evidence standard —
   > that is not the same as saying only 203 facilities have an ICU: 2,867 carry
   > unverified claims waiting for review. And the 6,890 red records mean *no ICU
   > evidence in a judgeable record* — our audit shows a third of them are dental
   > practices, labs and clinics that were never going to have one. The facility level
   > states evidence; only the regional layer talks about planning."

2. **(0:15–0:30) Desert vs gap.** Open the ⚪ district, then the 🔴 district.
   > "This district's records are too thin to judge — *Insufficient data to assess*.
   > CareGap Map refuses to call it an ICU desert. This other district is judgeable and
   > shows no credible ICU evidence anywhere — *Potential planning gap*. Same colour
   > logic, opposite meaning, never conflated."

3. **(0:30–0:55) Facility drilldown.** Open one 🔴 record, then one 🟢 record.
   - on the red record: *"This judgeable record contains no credible ICU evidence. The
     regional layer decides whether that pattern becomes a potential planning gap."*
   - point at the three separate readings: **ICU evidence strength**, **record
     judgeability**, and the **planning-readiness checklist** ("judgeable does not mean
     planning-ready — this one has no capacity and no doctor count")
   - on the green record: the **exact fragment from the supplied record** ("22-bed
     Intensive Care Unit …"), **distinct evidence categories** ("a marketing phrase
     can't corroborate itself"), ICU subtype where applicable ("NICU only — no general
     adult ICU claim")

4. **(0:55–1:20) Save a planning scenario.** On the 🔴 district:
   > "I save this as a scenario — selection, metrics, my note, the scoring-config
   > fingerprint and the data snapshot."
   Refresh the page, reopen the scenario — the filters and numbers come back
   (SQLite locally, Delta tables on Databricks, surviving restarts).

5. **(1:20–1:30) Close.**
   > "Deterministic, configurable, audited — we published the audit of our own headline
   > numbers, renamed everything that could be over-read, and no metric here claims to
   > verify real clinical availability. That's the trust layer."

## Honesty guardrails (never say)

- "99 % of Indian facilities are fully documented" (99 % is *record judgeability* —
  populated fields, not verified documentation and not planning readiness)
- "68 % of India lacks ICU care" (6,890 records without ICU evidence ≠ regional
  conclusions; many are non-hospital organizations)
- "2 % national ICU coverage" (2 % is the *trusted-record share* of supplied records)
- any verified clinical claim ("this hospital HAS an ICU" / "the app verifies real
  clinical availability")
- "one green state has adequate coverage" (green means *trusted evidence exists*)
- patient recommendations or referrals
- that geographic access / travel time / population need is measured

## Fallback

If the app fails: `python -m pytest` (250+ green tests),
`reports/headline_metric_audit.md`, `data/processed/cleaning_summary.json` and
`data/processed/llm_comparison.json` tell the story from the terminal.
