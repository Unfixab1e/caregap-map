"""Central configuration: paths, keywords, weights, thresholds, labels.

Every tunable used by the evidence extractor, validators, scorers and
regional aggregation lives here. Nothing in the rest of the code base
hardcodes a weight or threshold.

Override mechanism: set the environment variable ``CAREGAP_SCORING_CONFIG``
to a JSON file; its keys override the defaults (see ``load_scoring_config``).
Data locations can be moved with ``CAREGAP_DATA_DIR``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Classification labels (the four product states)
# ---------------------------------------------------------------------------

CLASS_TRUSTED = "Trusted ICU Coverage"
CLASS_LIKELY_GAP = "Likely Medical Gap"
CLASS_INSUFFICIENT = "Insufficient Data"
CLASS_NEEDS_REVIEW = "Needs Human Review"

ALL_CLASSES = [CLASS_TRUSTED, CLASS_LIKELY_GAP, CLASS_INSUFFICIENT, CLASS_NEEDS_REVIEW]

# User-facing display labels for the facility classes (D19). The stored
# constants above stay stable for Parquet/tests/history compatibility, but
# the wording shown to users must not overstate what a record proves:
# "Likely Medical Gap" reads as a real-world claim, while the defensible
# statement is that a judgeable record contains no credible ICU evidence.
# "Coverage" is likewise reserved for actual geographic/population coverage,
# which this dataset cannot measure.
FACILITY_DISPLAY_LABELS = {
    CLASS_TRUSTED: "Trusted ICU evidence",
    CLASS_NEEDS_REVIEW: "Needs Human Review",
    CLASS_LIKELY_GAP: "No ICU evidence in judgeable record",
    CLASS_INSUFFICIENT: "Insufficient Data",
}


def facility_display_label(classification: str) -> str:
    """User-facing wording for a stored facility classification.

    Unknown values (e.g. region statuses routed through shared UI helpers)
    pass through unchanged.
    """
    return FACILITY_DISPLAY_LABELS.get(classification, classification)

# Subtype label for unqualified ICU / intensive-care claims.
SUBTYPE_GENERAL = "general_or_unspecified"

# Human-readable subtype labels used by the UI and reports.
SUBTYPE_LABELS = {
    "neonatal_icu": "NICU (neonatal)",
    "pediatric_icu": "PICU (paediatric)",
    "cardiac_icu": "cardiac ICU / ICCU",
    "medical_icu": "MICU (medical)",
    "surgical_icu": "SICU (surgical)",
    SUBTYPE_GENERAL: "general / unspecified ICU",
}

# Region-level status labels. Deliberately DIFFERENT wording from the
# facility classes: a region containing one trusted facility has trusted
# EVIDENCE, not sufficient coverage - the labels must never imply adequacy,
# population need, bed availability, travel time or clinical verification.
REGION_TRUSTED = "Trusted ICU evidence found"
REGION_PLANNING_GAP = "Potential planning gap"
REGION_DATA_DESERT = "Insufficient data to assess"
REGION_NEEDS_REVIEW = "Needs facility verification"

REGION_DISCLAIMER = (
    "This does not measure population need, bed availability, travel time, "
    "physical accessibility, or clinically verified service status."
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


class DataPaths(BaseModel):
    """Filesystem layout for the local (CSV/Parquet) data source."""

    data_dir: Path = Path("data")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def facilities_csv(self) -> Path:
        return self.raw_dir / "facilities.csv"

    @property
    def pin_directory_csv(self) -> Path:
        return self.raw_dir / "india_post_pincode_directory.csv"

    @property
    def nfhs_csv(self) -> Path:
        return self.raw_dir / "nfhs_5_district_health_indicators.csv"

    @property
    def facilities_clean_parquet(self) -> Path:
        return self.processed_dir / "facilities_clean.parquet"

    @property
    def facilities_scored_parquet(self) -> Path:
        return self.processed_dir / "facilities_scored.parquet"

    @property
    def pin_agg_parquet(self) -> Path:
        return self.processed_dir / "pin_directory_agg.parquet"

    @property
    def nfhs_clean_parquet(self) -> Path:
        return self.processed_dir / "nfhs_clean.parquet"

    @property
    def region_state_parquet(self) -> Path:
        return self.processed_dir / "region_summary_state.parquet"

    @property
    def region_district_parquet(self) -> Path:
        return self.processed_dir / "region_summary_district.parquet"

    @property
    def cleaning_summary_json(self) -> Path:
        return self.processed_dir / "cleaning_summary.json"

    @property
    def reviews_db(self) -> Path:
        return self.data_dir / "reviews.db"


def default_paths() -> DataPaths:
    """Paths rooted at ``CAREGAP_DATA_DIR`` (default: ./data)."""
    return DataPaths(data_dir=Path(os.environ.get("CAREGAP_DATA_DIR", "data")))


# ---------------------------------------------------------------------------
# Evidence keywords
# ---------------------------------------------------------------------------
# All patterns are matched case-insensitively as regular expressions.
# Keyword presence is treated as a *signal*, never as proof of clinical
# capability — the scorer and the UI both say so explicitly.


class EvidenceKeywords(BaseModel):
    """Regex patterns per signal group, matched case-insensitively."""

    explicit_icu: list[str] = [
        r"\bicu\b",
        r"\bi\.c\.u\b",
        r"\biccu\b",
        r"\bnicu\b",
        r"\bpicu\b",
        r"\bmicu\b",
        r"\bsicu\b",
        r"intensive\s+care",
        r"critical\s+care",
    ]
    # Specialty-tag tokens that provide CONTEXT, never an explicit ICU claim.
    # Provenance: the upstream specialty classifier maps facility NAMES to
    # tags (e.g. "Trauma" in a name -> criticalCareMedicine), so these tokens
    # can exist without any ICU statement in the source content.
    specialty_context: list[str] = [
        r"criticalcare",  # camelCase criticalCareMedicine token in `specialties`
    ]
    # Phrases indicating the fragment may describe ANOTHER organization
    # (directory listings, referral/empanelment lists, partner pages) - the
    # upstream pipeline extracted organizations from multi-facility pages.
    cross_organization: list[str] = [
        r"\blisted\s+(as|in|under)\b",
        r"\bdirectory\b",
        r"\breferral\s+hospital\b",
        r"\bempanell?ed\b",
        r"\bpartner\s+(hospital|organi[sz]ation)\b",
    ]
    equipment: list[str] = [
        r"ventilator",
        r"\becmo\b",
        r"multi[- ]?para(meter)?\s+monitor",
        r"defibrillator",
        r"\bbipap\b",
        r"\bcpap\b",
        r"infusion\s+pump",
        r"syringe\s+pump",
        r"central\s+oxygen",
        r"oxygen\s+pipeline",
    ]
    procedure: list[str] = [
        r"intensive\s+care",
        r"critical\s+care",
        r"mechanical\s+ventilation",
        r"intubation",
        r"life\s+support",
        r"resuscitation",
    ]
    staffing: list[str] = [
        r"intensivist",
        r"critical\s+care\s+(specialist|physician|team|nurse|nursing)",
        r"icu\s+(staff|nurse|nursing|team)",
        r"anaesthesi(a|ologist|ology).{0,30}critical",
    ]
    # Phrases that *negate* an ICU claim. Matched against the same text.
    negation: list[str] = [
        r"\bno\s+(icu|intensive\s+care|critical\s+care|ventilator)",
        r"\bwithout\s+(an?\s+)?(icu|intensive\s+care|ventilator)",
        r"\blacks?\s+(an?\s+)?(icu|intensive\s+care|ventilator)",
        r"\bicu\s+(is\s+)?not\s+available",
        r"\bdoes\s+not\s+(have|offer|provide)\s+(an?\s+)?(icu|intensive\s+care)",
    ]
    # ICU subtype detection over explicit-claim fragments. An explicit
    # fragment matching none of these is "general_or_unspecified". NICU/PICU/
    # ICCU evidence must never be displayed as confirmed general adult ICU.
    subtypes: dict[str, list[str]] = {
        "neonatal_icu": [r"\bnicu\b", r"neonatal\s+(intensive|critical)\s+care"],
        "pediatric_icu": [r"\bpicu\b", r"p(?:ae|e)diatric\s+(intensive|critical)\s+care"],
        "cardiac_icu": [
            r"\biccu\b",
            r"\bcicu\b",
            r"cardiac\s+(intensive|critical)\s+care",
            r"coronary\s+care\s+unit",
            r"cardiac\s+icu",
        ],
        "medical_icu": [r"\bmicu\b", r"medical\s+(intensive|critical)\s+care", r"medical\s+icu"],
        "surgical_icu": [
            r"\bsicu\b",
            r"surgical\s+(intensive|critical)\s+care",
            r"surgical\s+icu",
            r"post[- ]?surgical\s+icu",
            r"post[- ]?operative\s+icu",
        ],
    }

    # Patterns that extract an ICU bed count (first capture group = count).
    # Each pattern requires the number, a bed word and ICU/intensive-care
    # context TOGETHER in one passage - that adjacency is the anchoring rule.
    icu_bed_count: list[str] = [
        r"(\d{1,4})\s*-?\s*bed(?:ded)?\s+(?:[a-z]*\s+)?icu",
        r"(\d{1,4})\s*-?\s*bed(?:ded)?\s+(?:[a-z]*\s+)?(?:intensive|critical)\s+care",
        r"icu[^.;]{0,30}?(\d{1,4})\s*beds?",
        r"(?:intensive|critical)\s+care[^.;]{0,30}?(\d{1,4})\s*beds?",
        r"(\d{1,4})\s*icu\s*beds?",
    ]


# Facility fields scanned for evidence, in display order.
EVIDENCE_TEXT_FIELDS = ["description", "capability", "specialties", "procedure", "equipment"]

# Fields whose raw values are JSON-encoded lists in facilities.csv.
LIST_FIELDS = ["capability", "specialties", "procedure", "equipment", "source_urls"]


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------


class EvidenceWeights(BaseModel):
    """Points contributed to the 0-100 capability evidence score."""

    explicit_claim: int = 35
    equipment: int = 20
    procedure: int = 15
    capacity: int = 10  # an extractable ICU bed count
    staffing: int = 10
    # Specialty-tag context (e.g. criticalCareMedicine) when NO explicit claim
    # exists - lands the record in the ambiguous review band, never trust.
    specialty_context: int = 20
    # Cross-field consistency: explicit claim appears across >= 3 record
    # fields. NOTE (provenance): capability/procedure/equipment were generated
    # together by ONE upstream extraction pass, so this measures internal
    # consistency of the supplied record - it is NOT independent confirmation
    # and does not count toward corroboration categories.
    multi_field_bonus: int = 10
    # Penalties (subtracted).
    negation_penalty: int = 40
    suspicious_penalty: int = 15


class CompletenessWeights(BaseModel):
    """Points contributed to the 0-100 data completeness score."""

    description: int = 20
    procedure: int = 15
    equipment: int = 15
    staffing: int = 10  # numberDoctors present
    capacity: int = 10
    source_url: int = 15
    geography: int = 15  # usable coordinates or a resolvable PIN code


class Thresholds(BaseModel):
    """Decision boundaries used by classification and aggregation."""

    # Facility level
    sufficient_completeness: int = 45  # >= this -> record is judgeable
    high_evidence: int = 45  # >= this (with sufficient data) -> Trusted ICU Coverage
    low_evidence: int = 15  # <= this (with sufficient data) -> Likely Medical Gap
    # Between low_evidence and high_evidence the record is ambiguous -> Needs Human Review.
    # Trusted additionally requires an explicit claim corroborated by at least
    # this many INDEPENDENT categories (equipment / procedure / staffing /
    # anchored bed count / multi-field). A signal produced by the same pattern
    # as the explicit claim itself (e.g. "critical care" matching both the
    # explicit and the procedure group) does not count as corroboration.
    min_corroboration_categories: int = 2

    # Region level
    region_min_facilities: int = 3  # fewer records than this -> data desert
    region_min_data_pct: float = 40.0  # % of facilities with sufficient data, below -> data desert
    region_min_trusted: int = 1  # >= this many trusted facilities -> trusted coverage


class LlmConfig(BaseModel):
    """Settings for the optional model-backed evidence extractor.

    The LLM only *extracts* evidence; scores, validation and classification
    always stay deterministic. The API key is read from ``OPENAI_API_KEY``
    and never stored in this config.
    """

    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_output_tokens: int = 2000
    request_timeout_s: float = 60.0
    # Price estimates (USD per 1M tokens) used ONLY for cost reporting and
    # the pre-run budget guard; update to your model's current pricing.
    input_cost_per_mtok: float = 0.15
    output_cost_per_mtok: float = 0.60


class ScoringConfig(BaseModel):
    """Bundle of everything the scoring pipeline needs."""

    keywords: EvidenceKeywords = Field(default_factory=EvidenceKeywords)
    evidence_weights: EvidenceWeights = Field(default_factory=EvidenceWeights)
    completeness_weights: CompletenessWeights = Field(default_factory=CompletenessWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    # A record with fewer than this many populated key fields is "suspiciously sparse".
    sparse_record_min_fields: int = 2


def load_env_file(path: str | Path = ".env") -> int:
    """Load ``KEY=VALUE`` lines from a .env file into ``os.environ``.

    Deliberately tiny (no python-dotenv dependency): comments and blank
    lines are skipped, surrounding quotes are stripped, and existing
    environment variables are NEVER overridden - the process environment
    always wins. Returns the number of variables set. Missing file is fine.
    """
    env_path = Path(path)
    if not env_path.exists():
        return 0
    loaded = 0
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_scoring_config(path: str | Path | None = None) -> ScoringConfig:
    """Load the scoring configuration.

    Precedence: explicit ``path`` argument, then the ``CAREGAP_SCORING_CONFIG``
    environment variable, then built-in defaults. The JSON file may override
    any subset of keys; unspecified values keep their defaults.
    """
    if path is None:
        path = os.environ.get("CAREGAP_SCORING_CONFIG")
    if path is None:
        return ScoringConfig()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ScoringConfig.model_validate(raw)
