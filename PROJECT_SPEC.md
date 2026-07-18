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

Facilities and regions are classified into exactly four states:

1. **Trusted ICU Coverage** — high evidence + sufficient data
2. **Likely Medical Gap** — low evidence + sufficient data
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

## Classification logic

| Condition | Class |
|---|---|
| Self-contradictory record | Needs Human Review |
| Completeness below judgeability threshold | Insufficient Data |
| High evidence, sufficient data, no suspicious flags | Trusted ICU Coverage |
| High evidence but suspicious claims | Needs Human Review |
| Low evidence, sufficient data | Likely Medical Gap |
| Mid-band (ambiguous) evidence | Needs Human Review |

All thresholds and weights live in `src/caregap_map/config.py` and can be overridden via a
JSON file pointed to by `CAREGAP_SCORING_CONFIG` (see DECISIONS.md D5).

## Required user workflow

1. Select ICU (fixed) and a state or district.
2. View trust-weighted regional coverage.
3. Clearly distinguish likely medical gaps from data deserts.
4. Select a risky region.
5. View the facilities behind the regional result.
6. Open a facility and inspect: original claims, exact supporting sentences, missing
   evidence, contradictions, suspicious claims, score breakdown.
7. Save a reviewer note or planning scenario
   (e.g. *"Verify these facilities before classifying this district as an ICU desert."*).

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
