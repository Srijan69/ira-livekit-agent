"""Human-editable Markdown workflows for repeatable IRA tasks."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_WORKFLOW_TEXT = 2000
SMALL_WORKFLOW_MAX_STEPS = 5
MIN_PREDICTION_SCORE = 0.45
MIN_PREDICTION_TERMS = 2
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "can",
    "could",
    "for",
    "i",
    "in",
    "me",
    "my",
    "please",
    "the",
    "this",
    "to",
    "want",
    "with",
    "would",
}
_TOKEN_ALIASES = {
    "favourite": "favorite",
    "launch": "open",
    "playlists": "playlist",
    "songs": "song",
    "started": "start",
    "starting": "start",
}
DETERMINISTIC_TOOLS = {
    "open_app",
    "browse_to",
    "open_spotify",
    "read_page",
    "list_page_elements",
    "click_element",
    "type_into_element",
    "go_back",
    "close_browser",
    "spotify_control",
    "spotify_play_playlist",
}


def default_workflows_path() -> Path:
    override = os.environ.get("IRA_WORKFLOWS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "workflows"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:80]


@dataclass(frozen=True)
class Workflow:
    slug: str
    name: str
    goal: str
    steps: list[str]
    notes: str = ""
    version: int = 1
    worker: str = ""
    linear: bool = True
    requires_screen: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    failure_policy: str = "stop"
    recovery: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    @property
    def complexity(self) -> str:
        return classify_workflow(self)


@dataclass(frozen=True)
class WorkflowPrediction:
    workflow: Workflow
    confidence: float
    matched_terms: list[str] = field(default_factory=list)


def _tokens(value: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", (value or "").lower().replace("_", " "))
    return [
        _TOKEN_ALIASES.get(token, token)
        for token in raw
        if token not in _QUERY_STOPWORDS
    ]


class WorkflowStore:
    def __init__(self, root: Path | str | None = None, opener=None) -> None:
        self.root = Path(root) if root is not None else default_workflows_path()
        self._opener = opener or self._open_with_shell

    @staticmethod
    def _open_with_shell(path: Path) -> None:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif os.name == "posix":
            subprocess.Popen(["xdg-open", str(path)])
        else:
            raise OSError("Opening folders is not supported on this host")

    def open_folder(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        self._opener(self.root)
        return f"Opened the workflows folder at {self.root}."

    def _path(self, name: str) -> Path:
        slug = slugify(name)
        if not slug:
            raise ValueError("Workflow name cannot be empty")
        return self.root / f"{slug}.md"

    def save(
        self,
        name: str,
        goal: str,
        steps: list[str],
        notes: str = "",
        **metadata,
    ) -> str:
        path = self._path(name)
        clean_steps = [
            " ".join(str(step).split()) for step in steps if str(step).strip()
        ]
        if not clean_steps:
            raise ValueError("Workflow needs at least one step")
        clean_name = " ".join((name or "").split())
        clean_goal = " ".join((goal or "").split())
        path.parent.mkdir(parents=True, exist_ok=True)
        workflow = Workflow(
            path.stem,
            clean_name,
            clean_goal,
            clean_steps,
            " ".join(notes.split()),
            version=int(metadata.get("version", 1)),
            worker=str(metadata.get("worker", "")),
            linear=bool(metadata.get("linear", True)),
            requires_screen=bool(metadata.get("requires_screen", False)),
            allowed_tools=list(metadata.get("allowed_tools", [])),
            prerequisites=list(metadata.get("prerequisites", [])),
            verification=list(metadata.get("verification", [])),
            failure_policy=str(metadata.get("failure_policy", "stop")),
            recovery=list(metadata.get("recovery", [])),
            history=list(metadata.get("history", [])),
        )
        content = [
            "---",
            f"name: {clean_name}",
            f"goal: {clean_goal}",
            f"version: {workflow.version}",
            f"worker: {workflow.worker or workflow.complexity}",
            f"linear: {'true' if workflow.linear else 'false'}",
            f"requires_screen: {'true' if workflow.requires_screen else 'false'}",
            f"allowed_tools: {', '.join(workflow.allowed_tools)}",
            f"failure_policy: {workflow.failure_policy}",
            "---",
            "",
            f"# {clean_name}",
            "",
            clean_goal,
            "",
            "## Steps",
            "",
        ]
        content.extend(f"{index}. {step}" for index, step in enumerate(clean_steps, 1))
        if workflow.prerequisites:
            content.extend(["", "## Prerequisites", ""])
            content.extend(f"- {item}" for item in workflow.prerequisites)
        if workflow.verification:
            content.extend(["", "## Verification", ""])
            content.extend(f"- {item}" for item in workflow.verification)
        if workflow.recovery:
            content.extend(["", "## Recovery", ""])
            content.extend(f"- {item}" for item in workflow.recovery)
        if workflow.history:
            content.extend(["", "## Execution History", ""])
            content.extend(f"- {item}" for item in workflow.history)
        if workflow.notes.strip():
            content.extend(["", "## Notes", "", " ".join(notes.split())])
        path.write_text("\n".join(content) + "\n", encoding="utf-8")
        return f"Saved workflow {clean_name}."

    def list(self) -> list[Workflow]:
        if not self.root.exists():
            return []
        return [
            workflow
            for path in sorted(self.root.glob("*.md"))
            if (workflow := self._read(path))
        ]

    def get(self, name: str) -> Workflow | None:
        slug = slugify(name)
        if not slug:
            return None
        return self._read(self.root / f"{slug}.md")

    def predict(self, request: str) -> WorkflowPrediction | None:
        """Select a saved workflow from a natural-language request.

        Matching is deliberately local and bounded: the workflow name and goal
        carry the most weight, while steps and notes provide supporting context.
        A tied or weak match returns ``None`` so IRA asks for clarification
        instead of executing the wrong desktop action.
        """
        query_tokens = _tokens(request)
        if not query_tokens:
            return None
        exact = self.get(request)
        if exact is not None:
            return WorkflowPrediction(exact, 1.0, query_tokens)

        query = set(query_tokens)
        scored: list[tuple[float, Workflow, list[str]]] = []
        for workflow in self.list():
            name_tokens = set(_tokens(workflow.name))
            goal_tokens = set(_tokens(workflow.goal))
            detail_tokens = set(
                _tokens(
                    " ".join([*workflow.steps, workflow.notes, *workflow.prerequisites])
                )
            )
            matched = [
                token
                for token in query_tokens
                if token in name_tokens | goal_tokens | detail_tokens
            ]
            if len(set(matched)) < MIN_PREDICTION_TERMS:
                continue
            score = (
                0.5 * len(query & name_tokens) / len(query)
                + 0.3 * len(query & goal_tokens) / len(query)
                + 0.2 * len(query & detail_tokens) / len(query)
            )
            if score >= MIN_PREDICTION_SCORE:
                scored.append((score, workflow, sorted(set(matched))))

        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best = scored[0]
        if len(scored) > 1 and best[0] - scored[1][0] < 0.10:
            return None
        return WorkflowPrediction(best[1], round(best[0], 2), best[2])

    def resolve(self, request: str) -> Workflow | None:
        """Resolve an exact workflow name or a confident natural-language match."""
        exact = self.get(request)
        if exact is not None:
            return exact
        prediction = self.predict(request)
        return prediction.workflow if prediction is not None else None

    def delete(self, name: str) -> str:
        path = self._path(name)
        if not path.exists():
            return "Workflow not found."
        path.unlink()
        return f"Deleted workflow {path.stem}."

    def render(self, workflow: Workflow) -> str:
        lines = [
            f"Task: {workflow.goal}",
            f"Worker: {workflow.complexity}",
            "Strict execution: use only the declared steps and tools; stop on failure.",
        ]
        if workflow.allowed_tools:
            lines.append(f"Allowed tools: {', '.join(workflow.allowed_tools)}")
        if workflow.prerequisites:
            lines.extend(
                ["Prerequisites:", *[f"- {item}" for item in workflow.prerequisites]]
            )
        lines.append("Steps:")
        lines.extend(f"{index}. {step}" for index, step in enumerate(workflow.steps, 1))
        if workflow.verification:
            lines.extend(
                ["Verification:", *[f"- {item}" for item in workflow.verification]]
            )
        if workflow.recovery:
            lines.extend(["Recovery:", *[f"- {item}" for item in workflow.recovery]])
        if workflow.notes:
            lines.extend(["Notes:", workflow.notes])
        return "\n".join(lines)[:MAX_WORKFLOW_TEXT]

    def _read(self, path: Path) -> Workflow | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        front, separator, body = text[4:].partition("---\n")
        if not separator:
            return None
        metadata: dict[str, str] = {}
        remainder = body
        for line in front.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        steps: list[str] = []
        notes = ""
        sections: dict[str, list[str]] = {}
        current = ""
        for line in remainder.splitlines():
            heading = re.match(r"^##\s+(.+)$", line.strip())
            if heading:
                current = heading.group(1).strip().lower()
                sections.setdefault(current, [])
            elif current == "notes" and line.strip():
                notes = f"{notes} {line.strip()}".strip()
            elif current == "steps":
                match = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
                if match:
                    steps.append(match.group(1).strip())
            elif current:
                value = re.sub(r"^\s*[-*]\s*", "", line).strip()
                if value:
                    sections[current].append(value)
            else:
                match = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
                if match:
                    steps.append(match.group(1).strip())
        name = metadata.get("name") or path.stem.replace("-", " ").title()
        goal = metadata.get("goal", "")

        def boolean(key: str, default: bool) -> bool:
            return metadata.get(key, str(default)).lower() in {"1", "true", "yes"}

        allowed = [
            item.strip()
            for item in metadata.get("allowed_tools", "").split(",")
            if item.strip()
        ]
        return Workflow(
            path.stem,
            name,
            goal,
            steps,
            notes,
            version=int(metadata.get("version", "1") or 1),
            worker=metadata.get("worker", ""),
            linear=boolean("linear", True),
            requires_screen=boolean("requires_screen", False),
            allowed_tools=allowed,
            prerequisites=sections.get("prerequisites", []),
            verification=sections.get("verification", []),
            failure_policy=metadata.get("failure_policy", "stop"),
            recovery=sections.get("recovery", []),
            history=sections.get("execution history", []),
        )


def classify_workflow(workflow: Workflow) -> str:
    """Choose a worker deterministically from workflow capabilities."""
    if len(workflow.steps) > SMALL_WORKFLOW_MAX_STEPS:
        return "powerful"
    if not workflow.linear or workflow.requires_screen:
        return "powerful"
    if workflow.failure_policy.lower() not in {"stop", "halt"}:
        return "powerful"
    if any(tool not in DETERMINISTIC_TOOLS for tool in workflow.allowed_tools):
        return "powerful"
    return "lightweight"
