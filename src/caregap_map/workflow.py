"""Planner workflow navigation: steps, state inference, sidebar rendering.

Pure presentation/navigation logic (D26): the four-step workflow
(Select → Understand → Review → Save) is inferred deterministically from
simple session facts - no browser scroll tracking, no persistent storage,
and no business classification logic. The sidebar links are plain HTML
fragment anchors (``#select-region`` …), which by construction change
only the URL fragment and never touch the ``?state=…&district=…`` query
parameters.
"""

from __future__ import annotations

from html import escape

from pydantic import BaseModel


class WorkflowStep(BaseModel):
    number: int
    key: str  # stable section anchor id
    title: str
    description: str


WORKFLOW_STEPS: list[WorkflowStep] = [
    WorkflowStep(
        number=1,
        key="select-region",
        title="Select a region",
        description="Choose a state or district to assess.",
    ),
    WorkflowStep(
        number=2,
        key="understand-evidence",
        title="Understand the evidence",
        description="See what the records show and why.",
    ),
    WorkflowStep(
        number=3,
        key="review-facilities",
        title="Review priority facilities",
        description="Inspect claims, missing evidence and validation flags.",
    ),
    WorkflowStep(
        number=4,
        key="save-scenario",
        title="Save a planning scenario",
        description="Record the result, notes and next actions.",
    ),
]

TOTAL_STEPS = len(WORKFLOW_STEPS)


class WorkflowState(BaseModel):
    """Current + completed steps, inferred from session facts only."""

    current: int
    completed: set[int]


def infer_workflow_state(
    *,
    region_selected: bool,
    facility_reviewed: bool,
    scenario_saved: bool,
) -> WorkflowState:
    """Deterministic current/completed inference.

    - No region selected → Step 1 is current (graceful default).
    - A region is selected → Step 2 (Steps 1 complete).
    - A facility was explicitly reviewed → Step 3 (Steps 1-2 complete).
    - A scenario was saved this session → Step 4 current AND complete.

    Earlier steps count as completed exactly when the workflow has moved
    past them; a saved scenario completes everything. Session state is the
    only storage - never a database field.
    """
    if scenario_saved:
        return WorkflowState(current=4, completed={1, 2, 3, 4})
    if facility_reviewed and region_selected:
        return WorkflowState(current=3, completed={1, 2})
    if region_selected:
        return WorkflowState(current=2, completed={1})
    return WorkflowState(current=1, completed=set())


def current_step_label(state: WorkflowState) -> str:
    """Compact main-content indicator, mirroring the sidebar state."""
    step = WORKFLOW_STEPS[state.current - 1]
    return f"Step {state.current} of {TOTAL_STEPS} — {step.title}"


def sidebar_workflow_html(state: WorkflowState) -> str:
    """The 'CareGap workflow' navigation cards as one HTML block.

    Each card is a fragment link (``href="#<anchor>"``) so clicking only
    moves the viewport - the state/district query parameters are untouched.
    Current/completed/upcoming are distinguished by symbol, weight, border
    and background - never by color alone.
    """
    cards: list[str] = []
    for step in WORKFLOW_STEPS:
        if step.number == state.current:
            css, symbol, state_note = "current", "●", "current step"
        elif step.number in state.completed:
            css, symbol, state_note = "done", "✓", "completed"
        else:
            css, symbol, state_note = "todo", "", "upcoming"
        cards.append(
            f'<a class="cg-wf-step {css}" href="#{step.key}" '
            f'aria-label="Step {step.number}: {escape(step.title)} ({state_note})">'
            f'<span class="cg-wf-mark" aria-hidden="true">{symbol}</span>'
            f'<span class="cg-wf-body">'
            f'<span class="cg-wf-title">{step.number}&nbsp;·&nbsp;{escape(step.title)}</span>'
            f'<span class="cg-wf-desc">{escape(step.description)}</span>'
            f"</span></a>"
        )
    return '<div class="cg-wf">' + "".join(cards) + "</div>"
