# Architecture & data decisions

Each entry: context → decision → consequence. Newest last.

## D1 — Geography comes from the PIN directory first, the state field second

The raw `address_stateOrRegion` column has 253 distinct values including city names,
abbreviations ("Mh", "Up"), misspellings ("Telengana", "Chattisgarh") and shifted-column
JSON blobs. The India Post PIN directory is authoritative and 98.3 % of facilities carry a
valid 6-digit PIN.

**Decision:** canonical state and district come from the PIN-code join; the state text
field is normalised via an explicit alias table (exact / alias / suffix match, method
recorded) and used only as a fallback. City names alone are never guessed into states.
Provenance is stored per row (`geo_source`, `state_field_method`) and disagreement between
the two sources raises a `state_field_conflicts_with_pin_directory` flag (66 rows).

**Consequence:** 9,701 of 10,077 facilities get a district; 99 stay `(unassigned)` and are
reported as such rather than dropped.

## D2 — Conservative deduplication only

All 11 duplicated `unique_id` groups in the raw file are byte-identical row pairs.

**Decision:** drop exact `unique_id` duplicates (keeping the most complete row) and only
*flag* same-name-same-city records (`possible_duplicate_facility`). No fuzzy merging.

## D3 — Keyword evidence is a signal, never proof

**Decision:** deterministic extraction records *which pattern matched which exact original
fragment in which field*. The UI and docs state that signals reflect dataset consistency,
not verified clinical capability. A negated mention ("no ICU") in a text item suppresses
positive matches from that item and raises a contradiction flag.

## D4 — Classification precedence

Contradiction → `Needs Human Review` wins over everything; then insufficient completeness →
`Insufficient Data`; then evidence thresholds. Mid-band evidence (between `low_evidence`
and `high_evidence`) is `Needs Human Review`, not a coin-flip into Trusted/Gap. A would-be
Trusted record with suspicious claims (ICU beds > capacity, zero capacity, uncorroborated
claim) is demoted to review.

**Consequence on real data:** 2,007 Trusted / 6,890 Likely Gap / 1,063 Needs Review /
117 Insufficient at facility level.

## D5 — All weights and thresholds in one config object

**Decision:** `ScoringConfig` (pydantic) in `src/caregap_map/config.py` holds every keyword
list, weight and threshold. `CAREGAP_SCORING_CONFIG=<path>.json` overrides any subset.
Nothing else in the codebase hardcodes a number that changes classification.

Current defaults: judgeable at completeness ≥ 45; trusted at evidence ≥ 45; likely gap at
evidence ≤ 15; region data desert below 40 % judgeable records or under 3 records.

## D6 — `icu_claim_not_in_description` is informational, not demoting

First implementation treated "ICU appears only in structured claim fields, not in the
description" as suspicious and demoted 1,680 records to review. But the `capability` field
in this dataset *is* extracted claim text and descriptions are often one-liners.

**Decision:** downgraded to `data_quality` severity — shown to reviewers, no class change.
Demotion is reserved for internally inconsistent claims.

## D7 — Region status separates gaps from deserts

**Decision:** a region with `< region_min_facilities` records or `< region_min_data_pct` %
judgeable records is a **Data Desert** regardless of what the few records say. Only a
region whose records are judgeable and show no credible ICU evidence becomes **Likely
Medical Gap**. Unresolved ambiguous facilities block the gap label (→ Needs Human Review).
Facilities without a resolved region aggregate under `(unassigned)` — never silently lost.

## D8 — Trust-weighted coverage metric

`trust_weighted_icu_coverage` = Σ(evidence/100 × completeness/100) / Σ(completeness/100),
range 0–1. Poorly documented claims move the needle less than well-documented ones.
Reported alongside — never instead of — separate evidence-coverage and data-coverage
percentages.

## D9 — Storage abstraction now, Databricks later

The app reads only through the `DataSource` protocol (`data_access.py`); reviewer notes go
through the `ReviewStore` protocol (`persistence.py`, SQLite locally). A
`DatabricksDataSource` stub marks the seam for the deployment milestone. Scoring code
imports neither Streamlit nor Databricks.

## D10 — NFHS is cleaned but not yet joined

NFHS-5 indicators are parsed with a robust numeric cleaner (handles `(29.5)`, `*`, text)
into `nfhs_clean.parquet` with canonicalised state names, but no district-level fuzzy join
is attempted in this milestone (state/district spellings differ across datasets; a
defensible join needs recorded match confidence — deferred).

## D11 — Chart colors are validated status colors

Classification colors follow the status convention (good/warning/critical/neutral-gray)
and the stack order (Trusted → Needs Review → Likely Gap → Insufficient) was chosen so
green and red are never adjacent; the palette passes color-vision-deficiency separation
checks in light and dark modes. "Insufficient Data" is deliberately gray: absence of data
should not carry a status hue. Classifications are always also shown as text/icons, never
color alone.

## D12 — LLM extraction is subordinate to deterministic verification

The optional OpenAI-backed extractor (`llm_extraction.py`) returns the same
`EvidenceResult` model as the deterministic extractor, but nothing it says is taken on
faith: every quoted fragment must be located verbatim (whitespace-tolerant) in the source
record and is re-anchored to the *source's own* text; unlocatable quotes are dropped and
counted in a `llm_unverified_fragments_dropped:N` suspicious flag. An explicit ICU claim
or bed count without a verified fragment behind it is ignored. Scoring, validators and
classification run deterministically on the result — the LLM can only change *which
evidence is found*, never *how it is judged*.

## D13 — Two Databricks data paths, volume-first

Deployment supports (A) the app reading processed Parquet from a FUSE-mounted Unity
Catalog volume through the unchanged `LocalDataSource` (fastest, fewest moving parts) and
(B) `DatabricksDataSource` reading registered UC tables via a SQL warehouse
(`CAREGAP_DATA_SOURCE=databricks`). The adapter validates identifiers, lazy-imports the
connector, and takes an injectable connection factory so it is unit-testable without a
workspace. The deployment steps are documented but not yet executed against a live
workspace (no credentials on the dev machine) — recorded honestly in TASKS.md.

## D14 — Trusted requires independent corroboration (calibration)

The manual review of the real-API LLM disagreements exposed a false-Trusted pattern: one
phrase containing "critical care" (e.g. inside a specialty enumeration or a staff list)
matches both the explicit-claim group and the procedure group, double-counting to 35+15 =
50 — exactly over the Trusted bar (observed on Fortis Kangra, Kirloskar Hospital).

**Decision:** Trusted now additionally requires (a) an explicit intensive-care claim and
(b) at least `min_corroboration_categories` (default 2, configurable) independent
corroboration categories among: equipment, procedure, staffing, anchored ICU bed count,
multi-field evidence. A signal produced by a pattern that also belongs to the
explicit-claim group does not count — one marketing phrase must not corroborate itself.
Distinct keywords inside one sentence ("ICU" + "ventilator") still count, because they
are different evidence.

**Before/after on the full dataset (10,077 facilities):**
Trusted 2,006 → 535; Needs Human Review 1,064 → 2,535; Likely Medical Gap and
Insufficient Data unchanged (6,890 / 117). Every demoted record moved to review — none
flipped to "gap", so the change cannot manufacture medical deserts. The demotion is
deliberately aggressive against false-Trusted risk (the product's worst failure mode);
planners see the 2,535 as an explicit verification worklist. Restoring looser behaviour
is one config value (`min_corroboration_categories: 1`), and the human-labelled
evaluation (evals/) exists to tune this with ground truth rather than taste.

## D15 — LLM bed counts must be re-derivable from one verified fragment

A model-reported ICU bed count is accepted only when the shared deterministic anchoring
patterns (number + bed word + ICU context together in one passage) re-derive it from a
verified fragment. "10 ventilators" + "ICU available" across fragments never yields 10
ICU beds; mismatching or unanchorable payload counts raise suspicious flags.

## D16 — ICU subtypes are surfaced, never equated

NICU/PICU/ICCU/MICU/SICU claims are detected via configurable patterns over
explicit-claim fragments (identically for both extractors) and shown in the drilldown.
A record whose only intensive-care evidence is a specialised subtype displays
"Intensive-care evidence found: NICU (neonatal) only — no general adult ICU claim";
no clinical-equivalence rules are applied.

## D17 — MLflow tracing deferred

The app's drilldown already exposes the full audit chain (source fields → extractor
provenance → verified fragments → subtype detection → validator flags → score components
→ classification → regional aggregation) directly in the UI, and every stage is stored in
the processed Parquet. MLflow run/trace logging is deferred until after the live
deployment is stable, per the milestone priority order.

## D18 — Dataset-generation provenance: generated fields are one source, not many

Inspection of the challenge's dataset-generation prompt files (facility_and_ngo_fields.py,
free_form.py, medical_specialties.py, organization_extraction.py — referenced, not
committed) established:

- `procedure`, `equipment` and `capability` are filled by **one extraction pass** over
  website text **and images** (equipment in photos, signage). Agreement across these
  fields is *cross-field consistency of one generated record*, never independent
  sources; some claims may have no original webpage sentence at all (image-derived).
- `capacity` means **total inpatient beds** and `numberDoctors` means **total doctors** —
  already treated that way here (bed-count anchoring; doctors only feed completeness).
- `description` is itself a generated summary; the upstream address prompt **mandates
  geography inference** from URL domains/phone numbers — our PIN-first geography with
  recorded provenance stands.
- The specialty classifier maps facility **names** to tags ("Trauma" →
  criticalCareMedicine), so a `criticalCareMedicine` tag proves nothing about an ICU.
- The organization-extraction prompt shows multi-facility pages (directories, referral/
  partner lists) feed the pipeline, so records can carry other organizations' content.

**Decisions:**
1. The camelCase specialty token is no longer an explicit ICU claim: new
   `specialty_context` signal group (weight 20, counted only when no explicit claim
   exists → lands in the review band, never trust; applied identically to LLM output).
2. Cross-field agreement is removed from the Trusted corroboration categories (now:
   equipment / procedure / staffing / anchored bed count — distinct evidence *types
   within the supplied record*); the small score bonus remains but is displayed as
   `cross_field_consistency`, and user-facing wording says "distinct evidence categories
   in the supplied record", not "independent corroboration".
3. New `directory_or_partner_content_detected` suspicious flag when ICU-relevant
   fragments contain directory/referral/partner phrases → routes would-be Trusted to
   review.
4. UI/docs wording layer-corrected: fragments are exact quotes **from the supplied
   facility record** (model-generated upstream), never "original hospital website
   sentences" or verified clinical facts.

The challenge utility files themselves are NOT imported (unclear redistribution rights,
one has a nonfunctional `fdr.config` dependency; their value is provenance, not code).

**Before/after on 10,077 facilities:** Trusted 535 → 203; Needs Human Review
2,535 → 2,867; Likely Medical Gap and Insufficient Data unchanged (6,890 / 117).
Explicit ICU claims dropped 3,010 → 2,514 — roughly 500 records' only "claim" was the
name-derived specialty tag. Every demotion went to review, none to gap (no manufactured
deserts). This is deliberately strict against false Trusted; the human-labelled
evaluation (evals/) is the instrument for relaxing it with ground truth
(`min_corroboration_categories`, `specialty_context` weight are config values).
