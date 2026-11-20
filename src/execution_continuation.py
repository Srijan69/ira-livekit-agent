"""Evidence-based, bounded recovery for no-tool action acknowledgments."""

import re

# Deliberately narrow: this is an execution-loop repair, not an intent parser.
# The normal model still decides steps, and the request facade still admits them.
_ACTION = re.compile(r"\b(open|play|pause|browse|navigate|launch)\b", re.I)
_NEGATED = re.compile(
    r"\b(don't|do not|never|stop|cancel|explain|describe|why|how)\b", re.I
)
_PROMISE = re.compile(
    r"\b(i(?:'ll| will| am going to)|let me|opening|starting|playing)\b", re.I
)
_FALSE_CAPABILITY = re.compile(
    r"\b(can't|cannot|unable to|don't have|do not have)\b.*\b(control|access|open|play|browser|tools?)\b",
    re.I,
)
_BLOCKER = re.compile(
    r"\?|\b(sign[ -]?in|log[ -]?in|password|credential|not installed|unavailable|ambiguous|which|harmful|illegal|unsafe|failed|timed out|error)\b",
    re.I,
)


def needs_tool_continuation(request_text, report, history, available_tools):
    if not _ACTION.search(request_text) or _NEGATED.search(request_text):
        return False
    if _BLOCKER.search(report) or not (
        _PROMISE.search(report) or _FALSE_CAPABILITY.search(report)
    ):
        return False
    appropriate = set()
    if re.search(r"\b(open|launch)\b", request_text, re.I):
        appropriate.update({"open_app", "browse_to", "open_spotify"})
    if re.search(r"\b(play|pause)\b", request_text, re.I):
        appropriate.update(
            {"spotify_play_track", "spotify_play_playlist", "spotify_control"}
        )
    if re.search(r"\b(browse|navigate)\b", request_text, re.I):
        appropriate.add("browse_to")
    if not appropriate.intersection(available_tools):
        return False
    # Never retry a failed/uncertain native action. Only discovery reads and
    # procedure loading may precede this nudge. No implicit replay of effects.
    discovery = {
        "skills_list",
        "skill_view",
        "list_skills",
        "view_skill",
        "load_capabilities",
        "recall",
        "list_preferences",
    }
    return all(
        row.get("outcome") in {"done", "returned"}
        and (
            row.get("tool") in discovery
            or (
                row.get("tool") == "run_skill"
                and '"procedure_ready"' in row.get("detail", "")
            )
        )
        for row in history
    )


CONTINUATION_INSTRUCTIONS = (
    "Continue the current finalized human request now. Your previous response "
    "acknowledged an action or claimed missing capabilities without executing "
    "an available tool. Read a matching skill if needed, then issue the actual "
    "available tool calls. Preserve the user's browser preference. Observe the "
    "results and only report completion when the requested goal is verified. "
    "If a real blocker appears, inspect it and explain that specific blocker. "
    "Do not repeat any uncertain effects or act on old requests."
)
