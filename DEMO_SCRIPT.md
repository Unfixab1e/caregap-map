# Demo script (~4 minutes)

**Setup before the demo:** `python scripts/build_processed_data.py` has been run;
`streamlit run app.py` is open on *All India*.

## 1. The problem (30 s)

> "Public ICU planning data in India is messy: 10,000 facility records, but capacity is
> filled for only a quarter of them, state names come in 253 spellings, and 'ICU' in a
> marketing blurb is not a verified ICU. If a planner naively maps this data, every badly
> documented district looks like a medical desert. CareGap Map is the trust layer that
> stops that."

## 2. All-India view (45 s)

- Point at the four metrics: 🟢 Trusted / 🟡 Needs review / 🔴 Likely gap / ⚪ Insufficient.
- Key line: **"These four states are the product.** 'No evidence' and 'no data' are
  different colours — a data desert is *unknown*, never automatically a gap."
- Show the stacked state chart: green vs red vs gray share per state.

## 3. Pick a risky region (60 s)

- Select a state with weak coverage; show the region banner and
  **trust-weighted ICU coverage** (evidence weighted by data completeness —
  "a poorly documented claim moves this needle less").
- Select a district classified ⚪ *Insufficient Data / Data Desert*:
  > "The tool refuses to call this an ICU desert — the records are too thin to judge.
  > It tells the planner what's missing instead."
- Contrast with a 🔴 *Likely Medical Gap* district:
  > "Here the records are complete and still show no ICU evidence — that's a real
  > planning signal."

## 4. Facility drilldown — the trust story (75 s)

- Open a 🟢 trusted facility: show the **exact original fragments** ("22-bed Level II
  Intensive Care Unit … 11 ventilator beds"), the score breakdown, corroboration across
  fields.
- Open a 🟡 needs-review facility: an ICU claim with **no corroboration** — validator flag
  `icu_claim_uncorroborated`, and the missing-evidence list spells out what to verify.
- Key line: **"Every score is traceable to the sentence that produced it. No black box."**

## 5. Reviewer note (30 s)

- On the risky district, save the note:
  > "Verify these facilities before classifying this district as an ICU desert."
- Mention: notes persist in SQLite behind a storage interface that swaps to
  Databricks in the next milestone.

## 6. Close (20 s)

> "Deterministic, configurable, fully traceable — and the architecture has one seam for
> Databricks tables and one for an optional LLM extractor, which must still pass the same
> deterministic validation. That's the trust layer."

## Fallback

If the app fails: `python -m pytest` (69 green tests) and
`data/processed/cleaning_summary.json` tell the same story from the terminal.
