"""Audit the Trusted-boundary population before changing evidence policy.

    python scripts/audit_trust_boundary.py [--data-dir data]

Boundary population: Needs Human Review + evidence >= trust threshold +
explicit ICU claim + exactly one corroboration category + judgeable + no
contradiction. Reports how many records would move under candidate
policies v2A (substantive description corroboration + 1 operational
category) and v2B (v2A + claim must also appear in a structured field).

Writes git-ignored reports/trust_boundary_audit.{json,md}; prints
aggregates only. The committed code and tests never need real data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.audit import categorize_for_audit  # noqa: E402
from caregap_map.config import CLASS_NEEDS_REVIEW, DataPaths, load_scoring_config  # noqa: E402
from caregap_map.evidence import extract_evidence  # noqa: E402
from caregap_map.scoring import find_substantive_description_claim  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-json", default="reports/trust_boundary_audit.json")
    parser.add_argument("--out-md", default="reports/trust_boundary_audit.md")
    args = parser.parse_args()

    config = load_scoring_config()
    t = config.thresholds
    paths = DataPaths(data_dir=Path(args.data_dir))
    scored = pd.read_parquet(paths.facilities_scored_parquet)

    boundary = scored[
        (scored["classification"] == CLASS_NEEDS_REVIEW)
        & (scored["capability_evidence_score"] >= t.high_evidence)
        & (scored["explicit_icu_claim"])
        & (scored["n_corroboration_categories"] == 1)
        & (scored["data_completeness_score"] >= t.sufficient_completeness)
        & (scored["n_contradiction_flags"] == 0)
    ].copy()

    rows = []
    for _, r in boundary.iterrows():
        fragments = json.loads(r["evidence_fragments_json"])
        explicit_fields = sorted({f["field"] for f in fragments if f["group"] == "explicit_icu"})
        flags = json.loads(r["validation_flags_json"])
        flag_names = {f["name"] for f in flags}
        has_suspicious = any(f["severity"] == "suspicious" for f in flags)
        evidence = extract_evidence(r.to_dict(), config)
        substantive = find_substantive_description_claim(evidence, config)
        subtypes = json.loads(r["icu_subtypes_json"] or "[]")
        category = json.loads(r["corroboration_categories_json"] or "[]")
        v2a = substantive is not None and not has_suspicious
        v2b = v2a and any(f != "description" for f in explicit_fields)
        rows.append(
            {
                "unique_id": r["unique_id"],
                "name": r["name"],
                "state": r["state_final"],
                "district": r["district_final"],
                "evidence_score": int(r["capability_evidence_score"]),
                "completeness": int(r["data_completeness_score"]),
                "claim_in_description": "description" in explicit_fields,
                "claim_only_structured": "description" not in explicit_fields,
                "explicit_fields": explicit_fields,
                "one_category": category[0] if category else None,
                "has_suspicious": has_suspicious,
                "directory_flag": "directory_or_partner_content_detected" in flag_names,
                "description_len": len(str(r.get("description") or "")),
                "subtype_only": bool(subtypes) and "general_or_unspecified" not in subtypes,
                "audit_category": categorize_for_audit(r.get("name"), r.get("organization_type")),
                "substantive_description": substantive is not None,
                "substantive_fragment": substantive,
                "moves_v2a": v2a,
                "moves_v2b": v2b,
            }
        )
    detail = pd.DataFrame(rows)

    def vc(col):
        return detail[col].value_counts(dropna=False).to_dict() if len(detail) else {}

    summary = {
        "boundary_population": len(detail),
        "claim_in_description": int(detail["claim_in_description"].sum()) if len(detail) else 0,
        "claim_only_structured": int(detail["claim_only_structured"].sum()) if len(detail) else 0,
        "by_one_category": vc("one_category"),
        "with_suspicious_flag": int(detail["has_suspicious"].sum()) if len(detail) else 0,
        "with_directory_flag": int(detail["directory_flag"].sum()) if len(detail) else 0,
        "subtype_only": int(detail["subtype_only"].sum()) if len(detail) else 0,
        "by_audit_category": vc("audit_category"),
        "by_state_top10": dict(Counter(detail["state"].fillna("(unassigned)")).most_common(10))
        if len(detail)
        else {},
        "description_length": {
            "median": float(detail["description_len"].median()) if len(detail) else None,
            "p25": float(detail["description_len"].quantile(0.25)) if len(detail) else None,
            "p75": float(detail["description_len"].quantile(0.75)) if len(detail) else None,
        },
        "substantive_description": int(detail["substantive_description"].sum()) if len(detail) else 0,
        "moves_under_v2a": int(detail["moves_v2a"].sum()) if len(detail) else 0,
        "moves_under_v2b": int(detail["moves_v2b"].sum()) if len(detail) else 0,
        "v2a_by_audit_category": detail[detail["moves_v2a"]]["audit_category"]
        .value_counts()
        .to_dict()
        if len(detail)
        else {},
        "v2a_by_category_type": detail[detail["moves_v2a"]]["one_category"]
        .value_counts()
        .to_dict()
        if len(detail)
        else {},
        "v2a_subtype_only": int(detail[detail["moves_v2a"]]["subtype_only"].sum())
        if len(detail)
        else 0,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps({"summary": summary, "records": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md = ["# Trust-boundary audit (policy v1 -> v2 candidates)", "", "```json"]
    md.append(json.dumps(summary, indent=2, ensure_ascii=False))
    md.append("```")
    md.append("")
    md.append("## Sample of candidate fragments (v2A movers)")
    if len(detail):
        for _, r in detail[detail["moves_v2a"]].head(15).iterrows():
            md.append(f"- **{r['name']}** ({r['one_category']}): “{r['substantive_fragment']}”")
    Path(args.out_md).write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nDetailed report (git-ignored): {out_json} / {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
