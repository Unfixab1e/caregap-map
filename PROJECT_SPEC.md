# CareGap Map — Project Specification (frozen scope)

**Challenge:** Databricks Data Legend — Building the Trust Layer for Indian Healthcare
**Mission:** Medical Desert Planner
**Capability:** ICU only
**Target user:** NGO / public-health planner evaluating ICU infrastructure coverage in India.

## Core idea

The product must distinguish a **likely real medical-service gap** from a gap caused by
**incomplete data**. The system must never treat *"no reliable ICU evidence"* as automatically
equivalent to *"no ICU exists."*

## The four states

Facilities and regions are classified into exactly four states (stored constants; the
UI shows the precise display wording of D19):

1. **Trusted ICU Coverage** (displayed *"Trusted ICU evidence"*) — high evidence +
   sufficient data
2. **Likely Medical Gap** (displayed *"No ICU evidence in judgeable record"*) — low
   evidence + sufficient data; the regional layer alone concludes "potential planning
   gap"
3. **Insufficient Data / Data Desert** — not enough information to judge
4. **Needs Human Review** — contradictory, suspicious, or ambiguous evidence

## Two independent scores

### Capability Evidence Score (0–100)

How strongly does the record support that the facility has ICU capability?

Evidence fields: `capability`, `description`, `specialties`, `procedure`, `equipment`,
`capacity`, `numberDoctors`, `source_urls`.

Evidence signals: explicit ICU / intensive-care claims, critical-care services,
ventilators and related equipment, ICU bed / capacity information, relevant procedures,
supporting staff information, corroboration across multiple fields.

### Data Completeness Score (0–100)

Is there enough information to judge the facility? Signals: description present,
procedure information present, equipment information present, staffing information
present, capacity information present, source URL present, usable coordinates or
geographic fields.

**The two scores are independent by construction** — completeness never looks at what is
claimed, evidence never looks at how much is filled in.

Four separately displayed concepts (D20, D23): **ICU evidence strength** (the evidence
score), **record judgeability** (the completeness score: are the fields populated
enough to evaluate the record's claims?), **operational data availability** (a
transparent six-item data checklist — location resolved, source/provenance, total
facility capacity, total doctor count, source-anchored ICU bed count, independent
ICU-relevant operational detail), and the **automated evidence assessment** (what the
deterministic rules concluded and whether a human still needs to review). Judgeability
is never presented as operational data availability or "full documentation", and the
checklist never feeds classification.

## Classification logic

| Condition | Class |
|---|---|
| Self-contradictory record | Needs Human Review |
| Completeness below judgeability threshold | Insufficient Data |
| High evidence + explicit claim + ≥2 distinct evidence categories | Trusted ICU Coverage |
| High evidence but suspicious claims, no explicit claim, or insufficient corroboration | Needs Human Review |
| Low evidence, sufficient data | Likely Medical Gap |
| Mid-band (ambiguous) evidence | Needs Human Review |

Evidence categories: equipment, procedure, staffing, anchored ICU bed count — distinct
evidence *types within the supplied record*, not independent sources (the upstream
pipeline generated `capability`/`procedure`/`equipment` together in one pass, so
cross-field agreement counts only as internal consistency; D14/D18). A signal produced by
the same pattern as the explicit claim itself never counts. Specialty tags such as
`criticalCareMedicine` are context (they can derive from the facility name alone) and
never create an ICU claim. ICU subtypes (NICU/PICU/ICCU/MICU/SICU) are detected and
surfaced; specialised-subtype-only evidence is never displayed as general adult ICU (D16).

Region-level statuses use separate wording ("Trusted ICU evidence found", "Potential
planning gap", "Insufficient data to assess", "Needs facility verification") because
evidence presence is not coverage sufficiency.

All thresholds and weights live in `src/caregap_map/config.py` and can be overridden via a
JSON file pointed to by `CAREGAP_SCORING_CONFIG` (see DECISIONS.md D5).

## Required user workflow

The UI presents this as four numbered stages (D24): Select region → Understand
the evidence → Review priority facilities → Save a planning scenario.

1. Select ICU (fixed) and a state or district.
2. View the regional evidence summary (trust-weighted ICU evidence index,
   trusted-record share, judgeable-record share).
3. Clearly distinguish evidence gaps from data deserts.
4. Select a risky region.
5. View the facilities behind the regional result.
6. Open a facility and inspect: supplied-record claims, exact supporting fragments,
   missing evidence, contradictions, suspicious claims, score breakdown, and the
   operational data availability checklist and automated evidence assessment.
7. Save a reviewer note (e.g. *"Verify these facilities before classifying this
   district as an ICU desert."*) and/or a structured **planning scenario** (selection +
   aggregate metrics + note; reopenable after refresh and restart — D22).

## Explicit non-goals

- No general healthcare chatbot, no diagnosis/treatment recommendations, no
  patient-facing referrals, no Referral Copilot mission.
- No support for capabilities other than ICU.
- No authentication or complex user accounts.
- No live external healthcare-data collection.
- No large autonomous multi-agent architecture.
- No medical claims not supported by the supplied records.
- Facility-level inspection is part of the Medical Desert Planner drilldown, not a
  separate second mission.

## Data

Local raw inputs (never committed, never modified):

- `data/raw/facilities.csv` — 10,088 rows × 51 cols
- `data/raw/india_post_pincode_directory.csv` — 165,627 rows × 11 cols
- `data/raw/nfhs_5_district_health_indicators.csv` — 706 rows × 109 cols (secondary)

## Architecture

Streamlit app (deployable as a Databricks App) over a `DataSource` adapter, so the same
scoring/aggregation code runs against local CSV/Parquet today and Databricks tables later.
Scoring logic is pure Python with no Streamlit or Databricks dependency.

OpenAI-backed extraction is a **later** milestone; it will implement the same interface as
the deterministic extractor, and its output must still pass deterministic validation.
