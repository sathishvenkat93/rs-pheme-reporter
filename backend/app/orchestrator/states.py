"""Session state machine (R1). Transitions and guards live here, in code — never in prompts."""

from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    INGEST = "INGEST"
    PROFILE = "PROFILE"
    PROPOSE = "PROPOSE"
    REFINE = "REFINE"
    APPROVED = "APPROVED"
    BUILD = "BUILD"
    PUBLISHED = "PUBLISHED"
    EDIT = "EDIT"
    REFRESH = "REFRESH"


TRANSITIONS: dict[State, frozenset[State]] = {
    State.INGEST: frozenset({State.PROFILE}),
    State.PROFILE: frozenset({State.PROPOSE, State.INGEST}),
    State.PROPOSE: frozenset({State.REFINE, State.APPROVED, State.INGEST}),
    State.REFINE: frozenset({State.REFINE, State.APPROVED, State.INGEST}),
    State.APPROVED: frozenset({State.BUILD, State.REFINE}),
    State.BUILD: frozenset({State.PUBLISHED, State.REFINE}),
    State.PUBLISHED: frozenset({State.EDIT, State.REFRESH}),
    State.EDIT: frozenset({State.PUBLISHED, State.EDIT}),
    State.REFRESH: frozenset({State.PUBLISHED}),
}

# R7: the tools each state exposes to the LLM. build_dashboard exists only in APPROVED.
TOOLS_BY_STATE: dict[State, tuple[str, ...]] = {
    State.INGEST: ("ingest_files", "query", "list_dashboards"),
    State.PROFILE: ("profile_dataset", "query", "list_dashboards"),
    State.PROPOSE: (
        "profile_dataset", "query", "recommend_visuals", "parse_chart_request", "check_feasibility",
        "update_plan", "render_preview", "list_dashboards",
    ),
    State.REFINE: (
        "profile_dataset", "query", "recommend_visuals", "parse_chart_request", "check_feasibility",
        "update_plan", "render_preview", "request_approval", "list_dashboards",
    ),
    State.APPROVED: ("profile_dataset", "query", "build_dashboard", "list_dashboards"),
    State.BUILD: ("profile_dataset", "query", "list_dashboards"),
    State.PUBLISHED: ("profile_dataset", "query", "get_dashboard", "list_dashboards"),
    State.EDIT: (
        "profile_dataset", "query", "parse_chart_request", "check_feasibility", "render_preview",
        "request_approval", "get_dashboard", "resolve_widget", "patch_dashboard", "revert_dashboard",
        "list_dashboards",
    ),
    State.REFRESH: ("ingest_files", "profile_dataset", "query", "list_dashboards"),
}  # fmt: skip


class TransitionError(RuntimeError):
    """A state change the machine doesn't allow."""


def check_transition(current: State, target: State) -> None:
    if target not in TRANSITIONS[current]:
        raise TransitionError(f"Can't go from {current.value} to {target.value}.")
