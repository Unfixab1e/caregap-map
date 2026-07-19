"""URL-state helpers for the app: region selection in query parameters.

A full page refresh restarts the Streamlit session, so the selected region
lives in the URL (``?state=...&district=...``). Only public region names
ever appear there - no identifiers, notes or other user content. Values
are requests, not truth: the app validates them against the loaded data
and silently falls back to All India when they do not match.
"""

from __future__ import annotations


def normalize_region_request(state_param: str | None, district_param: str | None) -> dict | None:
    """Normalize raw query-parameter values into a region request.

    Returns ``{"state": ..., "district": ...}`` (district may be ``None``)
    or ``None`` when no usable state was requested. A district without a
    state is meaningless and ignored. Whitespace-only values count as
    absent. Membership validation happens later against the actual data -
    an unknown state or district must fall back to All India, never error.
    """
    state = (state_param or "").strip() or None
    district = (district_param or "").strip() or None
    if state is None:
        return None
    return {"state": state, "district": district}


def desired_region_params(state: str, district: str | None) -> dict[str, str]:
    """The query parameters that should represent the current selection."""
    desired: dict[str, str] = {}
    if state and state != "All India":
        desired["state"] = state
        if district:
            desired["district"] = district
    return desired
