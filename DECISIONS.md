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
