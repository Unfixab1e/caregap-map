"""Workflow navigation: step definitions, state inference, sidebar HTML."""

from __future__ import annotations

from caregap_map.workflow import (
    TOTAL_STEPS,
    WORKFLOW_STEPS,
    current_step_label,
    infer_workflow_state,
    sidebar_workflow_html,
)


class TestStepDefinitions:
    def test_four_steps_in_order(self):
        assert TOTAL_STEPS == 4
        assert [s.number for s in WORKFLOW_STEPS] == [1, 2, 3, 4]

    def test_titles_descriptions_and_anchors(self):
        expected = [
            ("select-region", "Select a region", "Choose a state or district to assess."),
            ("understand-evidence", "Understand the evidence", "See what the records show and why."),
            (
                "review-facilities",
                "Review priority facilities",
                "Inspect claims, missing evidence and validation flags.",
            ),
            (
                "save-scenario",
                "Save a planning scenario",
                "Record the result, notes and next actions.",
            ),
        ]
        for step, (key, title, description) in zip(WORKFLOW_STEPS, expected, strict=True):
            assert step.key == key
            assert step.title == title
            assert step.description == description


class TestInference:
    def test_no_region_highlights_step_one(self):
        state = infer_workflow_state(
            region_selected=False, facility_reviewed=False, scenario_saved=False
        )
        assert state.current == 1
        assert state.completed == set()

    def test_selected_region_highlights_step_two(self):
        state = infer_workflow_state(
            region_selected=True, facility_reviewed=False, scenario_saved=False
        )
        assert state.current == 2
        assert state.completed == {1}

    def test_explicit_facility_review_highlights_step_three(self):
        state = infer_workflow_state(
            region_selected=True, facility_reviewed=True, scenario_saved=False
        )
        assert state.current == 3
        assert state.completed == {1, 2}

    def test_saved_scenario_highlights_and_completes_step_four(self):
        state = infer_workflow_state(
            region_selected=True, facility_reviewed=True, scenario_saved=True
        )
        assert state.current == 4
        assert state.completed == {1, 2, 3, 4}

    def test_facility_review_without_region_falls_back_gracefully(self):
        # Cannot review a facility without a region: fall back to Step 1.
        state = infer_workflow_state(
            region_selected=False, facility_reviewed=True, scenario_saved=False
        )
        assert state.current == 1

    def test_reopened_scenario_defaults_to_step_two(self):
        # A reopened scenario restores the region; nothing else is set yet.
        state = infer_workflow_state(
            region_selected=True, facility_reviewed=False, scenario_saved=False
        )
        assert state.current == 2
        assert 1 in state.completed


class TestIndicator:
    def test_label_matches_sidebar_state(self):
        state = infer_workflow_state(
            region_selected=True, facility_reviewed=False, scenario_saved=False
        )
        assert current_step_label(state) == "Step 2 of 4 — Understand the evidence"

    def test_label_for_every_step(self):
        for step in WORKFLOW_STEPS:
            state = infer_workflow_state(
                region_selected=step.number >= 2,
                facility_reviewed=step.number >= 3,
                scenario_saved=step.number >= 4,
            )
            assert current_step_label(state).startswith(f"Step {step.number} of 4")
            assert step.title in current_step_label(state)


class TestSidebarHtml:
    def test_all_steps_with_anchor_links(self):
        html = sidebar_workflow_html(
            infer_workflow_state(region_selected=True, facility_reviewed=False, scenario_saved=False)
        )
        for step in WORKFLOW_STEPS:
            assert f'href="#{step.key}"' in html
            assert step.title in html
            assert step.description in html

    def test_fragment_links_never_touch_query_parameters(self):
        # Pure #fragment hrefs preserve ?state=...&district=... by construction.
        html = sidebar_workflow_html(
            infer_workflow_state(region_selected=True, facility_reviewed=True, scenario_saved=False)
        )
        assert 'href="#' in html
        assert "?" not in html
        assert "state=" not in html and "district=" not in html
        assert "javascript" not in html.lower()  # no custom JS navigation

    def test_states_are_marked_by_symbol_and_class_not_color_alone(self):
        html = sidebar_workflow_html(
            infer_workflow_state(region_selected=True, facility_reviewed=True, scenario_saved=False)
        )
        assert html.count('class="cg-wf-step done"') == 2  # steps 1-2
        assert html.count('class="cg-wf-step current"') == 1  # step 3
        assert html.count('class="cg-wf-step todo"') == 1  # step 4
        assert "✓" in html and "●" in html
        assert 'aria-label="Step 3: Review priority facilities (current step)"' in html

    def test_saved_state_marks_step_four_complete_and_current(self):
        html = sidebar_workflow_html(
            infer_workflow_state(region_selected=True, facility_reviewed=True, scenario_saved=True)
        )
        # Current takes visual precedence on step 4; the first three are done.
        assert html.count('class="cg-wf-step done"') == 3
        assert 'aria-label="Step 4: Save a planning scenario (current step)"' in html
