# Demo script — the 90-second flow (final build)

**Setup:** live app open (https://caregap-map-7474654537485030.aws.databricksapps.com),
demo candidates pre-identified via `python scripts/find_demo_facilities.py`
(one ⚪ data-desert district, one 🔴 planning-gap district, one 🟢 trusted
general-ICU record, one subtype-only record) in browser tabs.

**Core line:** *"No ICU evidence and not enough data to know are different planning
situations."*

## The 90 seconds

1. **(0:00–0:12) Headline.** All-India view.
   > "Only **203 of 10,077** supplied records meet our strict Trusted ICU evidence
   > standard. **That is not the same as saying only 203 facilities have an ICU** —
   > 2,867 carry unverified claims waiting for human review, and our published audit
   > shows a third of the red records are dental practices, labs and clinics that were
   > never going to have one."

2. **(0:12–0:25) Data Desert vs Potential Planning Gap.** Open the ⚪ district, then the 🔴 one.
   > "This district **lacks enough trustworthy information to assess** — we refuse to
   > call it an ICU desert. This other district is different: **these records are
   > judgeable, but none contains credible ICU evidence. This is still not a clinically
   > verified absence** — it's where a planner sends the verification team first."

3. **(0:25–0:50) Facility drilldown.** Open the 🔴 record, then the 🟢 record.
   - red: *"This judgeable record contains no credible ICU evidence. The regional layer
     decides whether that pattern becomes a potential planning gap."*
   - green: the **exact supplied-record fragment** ("22-bed Intensive Care Unit …"),
     **distinct evidence categories** ("a marketing phrase can't corroborate itself"),
     **ICU subtype** ("NICU only — no general adult ICU claim" where applicable),
     a **validator flag**, the **missing evidence** list, and the
     **operational data availability checklist** ("judgeable does not mean the
     operational data is there — this one has no capacity and no doctor count").

4. **(0:50–1:10) Save and reopen a planning scenario.** On the 🔴 district:
   > "I save this as a scenario — selection, metrics, my note, the scoring-config
   > fingerprint and the data snapshot." Refresh — the region selection survives via
   > the URL; reopen the scenario — filters and numbers come back. Notes and scenarios
   > live in **Delta tables** and survive full app restarts.

5. **(1:10–1:22) Databricks architecture.**
   > "Databricks is the governed operating layer: the **App** hosts the UI, the
   > processed evidence snapshot sits in a **Unity Catalog volume**, notes and
   > scenarios persist in **Delta** through the **SQL warehouse** with the app's
   > service-principal identity, and our evaluation runs as **MLflow 3 traces** — one
   > trace per facility, a span per pipeline stage. CareGap Map is the trust and
   > planning logic on top."

6. **(1:22–1:30) Close.**
   > "**The model may propose evidence, but only source-verifiable evidence affects
   > the score.** That's the trust layer."

## If asked about evaluation (say exactly this)

> “The challenge provides no ground-truth answer key, so we do not claim
> clinical accuracy. We evaluate the trust pipeline through deterministic
> validators, exact-fragment verification, adversarial audit samples and
> disagreement analysis across independent extractors.”

> “Model agreement is diagnostic, not accuracy. We intentionally traced
> difficult disagreement cases to expose where a human reviewer is needed.”

## Honesty guardrails (never say)

- "99 % of Indian facilities are fully documented" (99 % is *record judgeability* —
  populated fields, not verified documentation and not operational data availability)
- "68 % of India lacks ICU care" (facility-level evidence absence ≠ regional
  conclusions; many are non-hospital organizations)
- "2 % national ICU coverage" (2 % is the *trusted-record share* of supplied records)
- any verified clinical claim ("this hospital HAS an ICU" / "the app verifies real
  clinical availability")
- "one green state has adequate coverage" (green means *trusted evidence exists*)
- patient recommendations or referrals
- that geographic access / travel time / population need is measured

## Fallback

If the live app fails: `streamlit run app.py` locally shows the identical build;
`python -m pytest` (300+ green tests), `reports/headline_metric_audit.md` and
`reports/mlflow_eval_summary.json` tell the story from the terminal.
