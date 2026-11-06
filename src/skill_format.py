"""The skill file format: parsing, validation and rendering.

A skill is a Markdown file a person can read and edit, with just enough
structure for the engine to run it strictly::

    ---
    name: Play Favorite Playlist
    description: Plays one of the boss's Spotify playlists in a browser.
    phrases: put on my music; play my funk songs
    version: 2
    source: compiled
    ---

    ## Goal
    Music from {playlist} is playing in {browser}.

    ## Inputs
    - playlist — which saved playlist to play. Default: "My funk songs".

    ## Steps
    1. **Open the browser** — Spotify needs a window to load in.
       - do: open_app app_name={browser}
       - check: window_title contains {browser} within 8s
       - if stuck: the Start menu can be slow; open it once more.

Each part has a different reader. The voice model sees only ``name`` and
``description`` (a one-line catalog entry). The thinker reads the purposes,
hints and Never rules when it compiles or repairs. The engine reads only the
``do:`` and ``check:`` lines, which is what keeps execution strict.

A step without a ``do:`` line is plain English, waiting to be compiled. That is
a valid skill, not a malformed one: it is how dictated skills start out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SOURCES = ("dictated", "learned", "corrected", "repaired", "edited", "compiled")
STATES = ("draft", "validated", "enabled")
CHECK_KINDS = (
    "window_title",
    "page_contains",
    "page_element",
    "looks_like",
    "active_window",
    "page_value",
    "pointer_position",
)

#: A wait with no stated limit still needs one, or a typo in a page check
#: would hang the run forever. Ten seconds covers a slow page load.
DEFAULT_WAIT_LIMIT = 10.0

_SECTION_ALIASES = {
    "goal": "goal",
    "inputs": "inputs",
    "before starting": "before",
    "prerequisites": "before",
    "preconditions": "before",
    "steps": "steps",
    "done when": "done",
    "never": "never",
    "notes": "notes",
}
_PARAM = re.compile(r"\{([A-Za-z_]\w*)\}")
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)$")


# -- model -------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """Something the engine can observe: a window, a page, or (slowly) a look."""

    kind: str
    value: str
    within: float | None = None


@dataclass(frozen=True)
class Action:
    """What a step does. Exactly one kind; only that kind's fields are set."""

    kind: str  # tool | wait | ask | say | check
    tool: str | None = None
    args: tuple[tuple[str, str], ...] = ()
    seconds: float | None = None
    until: Check | None = None
    param: str | None = None
    text: str | None = None
    check: Check | None = None


@dataclass(frozen=True)
class Step:
    number: int
    purpose: str
    why: str = ""
    action: Action | None = None
    check: Check | None = None
    if_stuck: str = ""
    # Where the step sat in its source file, for error messages. Not part of
    # what the step *is*: re-rendering a file moves lines without changing a
    # single step, so two steps differing only here are the same step.
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class SkillInput:
    name: str
    description: str
    default: str | None = None

    @property
    def required(self) -> bool:
        return self.default is None


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    steps: tuple[Step, ...]
    phrases: tuple[str, ...] = ()
    version: int = 1
    source: str = "dictated"
    goal: str = ""
    inputs: tuple[SkillInput, ...] = ()
    preconditions: tuple[str, ...] = ()
    done_when: str = ""
    done_check: Check | None = None
    never: tuple[str, ...] = ()
    notes: str = ""
    allowed_tools: tuple[str, ...] = ()
    state: str = "draft"
    pinned: bool = False
    source_run_id: str = ""

    @property
    def compiled(self) -> bool:
        """True when every step has a ``do:`` line the engine can run."""
        return all(step.action is not None for step in self.steps)

    def input_named(self, name: str) -> SkillInput | None:
        return next((item for item in self.inputs if item.name == name), None)


@dataclass(frozen=True)
class Issue:
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}" if self.line else self.message


class SkillFormatError(ValueError):
    """Every problem in a file at once, each with its line number."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(str(issue) for issue in self.issues))


class _LineError(ValueError):
    """A problem on one line; the caller attaches the line number."""


# -- tokens ------------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    text: str
    quoted: bool = False
    key: str | None = None


def _read_quoted(source: str, start: int) -> tuple[str, int]:
    """Read a double-quoted string at ``start``; return (value, index after)."""
    out: list[str] = []
    i = start + 1
    while i < len(source):
        char = source[i]
        if char == "\\" and i + 1 < len(source):
            out.append(source[i + 1])
            i += 2
            continue
        if char == '"':
            return "".join(out), i + 1
        out.append(char)
        i += 1
    raise _LineError("a quoted value is missing its closing quote")


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(source):
        if source[i].isspace():
            i += 1
            continue
        key = None
        match = re.match(r"([A-Za-z_]\w*)=", source[i:])
        if match:
            key = match.group(1)
            i += match.end()
            if i >= len(source) or source[i].isspace():
                raise _LineError(f"'{key}=' has no value")
        if source[i] == '"':
            value, i = _read_quoted(source, i)
            tokens.append(_Token(value, True, key))
            continue
        end = i
        while end < len(source) and not source[end].isspace() and source[end] != '"':
            end += 1
        tokens.append(_Token(source[i:end], False, key))
        i = end
    # "(max 15s)" is the friendly spelling of "within 15s".
    merged: list[_Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            not token.quoted
            and token.text == "(max"
            and index + 1 < len(tokens)
            and tokens[index + 1].text.endswith(")")
        ):
            merged.append(_Token("within"))
            merged.append(_Token(tokens[index + 1].text[:-1]))
            index += 2
            continue
        merged.append(token)
        index += 1
    return merged


def _duration(text: str) -> float:
    match = _DURATION.match(text)
    if not match:
        raise _LineError(f"'{text}' is not a duration like 10s or 500ms")
    amount = float(match.group(1))
    return amount / 1000 if match.group(2) == "ms" else amount


def _parse_check(tokens: list[_Token]) -> Check:
    if not tokens:
        raise _LineError('a check needs a kind, like page_contains "text"')
    head = tokens[0].text.lower()
    rest = tokens[1:]
    if head == "looks" and rest and rest[0].text.lower() == "like":
        head, rest = "looks_like", rest[1:]
    if head == "window_title":
        if not rest or rest[0].text.lower() != "contains":
            raise _LineError("window_title checks read: window_title contains <text>")
        rest = rest[1:]
    if head not in CHECK_KINDS:
        raise _LineError(
            f"unknown check '{tokens[0].text}' "
            f"(expected one of: {', '.join(CHECK_KINDS)})"
        )
    if not rest:
        raise _LineError(f"{head} needs a value to look for")
    value = rest[0].text
    within = None
    tail = rest[1:]
    if tail:
        if tail[0].text.lower() != "within" or len(tail) != 2:
            raise _LineError(f"unexpected '{tail[0].text}' after the check value")
        if head == "looks_like":
            raise _LineError("looks like checks run once; they can't wait 'within'")
        within = _duration(tail[1].text)
    return Check(head, value, within)


def _parse_action(source: str) -> Action:
    tokens = _tokenize(source)
    if not tokens:
        raise _LineError("do: is empty")
    head = tokens[0]
    word = head.text.lower() if not head.quoted and head.key is None else ""
    rest = tokens[1:]

    if word == "wait":
        if not rest:
            raise _LineError("wait needs a duration (wait 10s) or a condition")
        if rest[0].text.lower() == "until":
            check = _parse_check(rest[1:])
            return Action(
                "wait",
                until=Check(
                    check.kind, check.value, check.within or DEFAULT_WAIT_LIMIT
                ),
            )
        if len(rest) != 1:
            raise _LineError("a fixed wait reads: wait 10s")
        try:
            return Action("wait", seconds=_duration(rest[0].text))
        except _LineError as exc:
            raise _LineError(f"a fixed wait reads: wait 10s; {exc}") from exc

    if word == "ask":
        if len(rest) != 2 or rest[0].quoted or not rest[1].quoted:
            raise _LineError('ask reads: ask <input> "question?"')
        return Action("ask", param=rest[0].text, text=rest[1].text)

    if word == "say":
        if len(rest) != 1 or not rest[0].quoted:
            raise _LineError('say reads: say "what to say"')
        return Action("say", text=rest[0].text)

    if word == "check":
        return Action("check", check=_parse_check(rest))

    if head.quoted or head.key is not None or not re.fullmatch(r"[a-z_]\w*", head.text):
        raise _LineError(f"'{head.text}' is not a tool name")
    args: list[tuple[str, str]] = []
    for token in rest:
        if token.key is None:
            raise _LineError(
                f"'{token.text}' needs a name: tool arguments read key=value"
            )
        args.append((token.key, token.text))
    return Action("tool", tool=head.text, args=tuple(args))


# -- sections ----------------------------------------------------------------

_STEP_START = re.compile(r"^\s{0,3}(\d+)[.)]\s+(.*)$")
_FIELD = re.compile(r"^\s+[-*]\s+([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_INPUT = re.compile(r"^\s*[-*]\s+([A-Za-z_]\w*)\s*(?:\u2014|\u2013|-|:)\s*(.*)$")
_DEFAULT = re.compile(
    r"\s*Default:\s*(?:\"((?:[^\"\\]|\\.)*)\"|(\S+?))\.?\s*$", re.IGNORECASE
)


def _split_purpose(text: str) -> tuple[str, str]:
    match = re.match(
        r"^\*\*(.+?)\*\*\s*(?:(?:\u2014|\u2013|-|:)\s*(.*))?$", text.strip()
    )
    if match:
        return match.group(1).strip(), (match.group(2) or "").strip()
    return text.strip(), ""


def _front_matter(lines: list[str], issues: list[Issue]) -> tuple[dict, int]:
    """Return (key → value, index of the first body line)."""
    if not lines or lines[0].strip() != "---":
        issues.append(Issue(1, "a skill file starts with --- front matter"))
        return {}, 0
    meta: dict[str, tuple[str, int]] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            return meta, index + 1
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = (value.strip(), index + 1)
        elif line.strip():
            issues.append(
                Issue(index + 1, f"front matter line '{line.strip()}' has no key")
            )
    issues.append(Issue(1, "front matter is missing its closing ---"))
    return meta, len(lines)


def _sections(lines: list[str], start: int) -> dict[str, list[tuple[int, str]]]:
    sections: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for index in range(start, len(lines)):
        line = lines[index]
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1).strip().lower()
            current = _SECTION_ALIASES.get(title, f"extra:{heading.group(1).strip()}")
            sections.setdefault(current, [])
            continue
        if line.startswith("# ") or current is None:
            continue
        sections[current].append((index + 1, line))
    return sections


def _prose(entries: list[tuple[int, str]]) -> str:
    return " ".join(line.strip() for _, line in entries if line.strip())


def _bullets(entries: list[tuple[int, str]]) -> tuple[str, ...]:
    items: list[str] = []
    for _, line in entries:
        match = _BULLET.match(line)
        if match:
            items.append(match.group(1).strip())
        elif line.strip() and items:
            items[-1] = f"{items[-1]} {line.strip()}"
    return tuple(items)


def _parse_inputs(
    entries: list[tuple[int, str]], issues: list[Issue]
) -> tuple[SkillInput, ...]:
    inputs: list[SkillInput] = []
    for line_no, line in entries:
        if not line.strip():
            continue
        match = _INPUT.match(line)
        if not match:
            issues.append(
                Issue(line_no, 'inputs read: - name — what it is. Default: "value".')
            )
            continue
        name, rest = match.group(1), match.group(2)
        default = None
        found = _DEFAULT.search(rest)
        if found:
            default = found.group(1) if found.group(1) is not None else found.group(2)
            if found.group(1) is not None:
                default = re.sub(r"\\(.)", r"\1", default)
            rest = rest[: found.start()]
        inputs.append(SkillInput(name, rest.strip(), default))
    return tuple(inputs)


def _parse_steps(
    entries: list[tuple[int, str]], issues: list[Issue]
) -> tuple[Step, ...]:
    steps: list[dict] = []
    for line_no, line in entries:
        if not line.strip():
            continue
        start = _STEP_START.match(line)
        if start:
            purpose, why = _split_purpose(start.group(2))
            steps.append(
                {"purpose": purpose, "why": why, "line": line_no, "fields": {}}
            )
            continue
        field_match = _FIELD.match(line)
        if field_match and steps:
            key = field_match.group(1).strip().lower()
            if key not in ("do", "check", "if stuck"):
                issues.append(
                    Issue(
                        line_no,
                        f"unknown step field '{key}' (expected do, check, if stuck)",
                    )
                )
                continue
            if key in steps[-1]["fields"]:
                issues.append(Issue(line_no, f"step has two '{key}' lines"))
                continue
            steps[-1]["fields"][key] = (line_no, field_match.group(2).strip())
            continue
        issues.append(Issue(line_no, f"unexpected line in Steps: '{line.strip()}'"))

    parsed: list[Step] = []
    for number, raw in enumerate(steps, 1):
        fields = raw["fields"]
        action = check = None
        if "do" in fields:
            line_no, text = fields["do"]
            try:
                action = _parse_action(text)
            except _LineError as exc:
                issues.append(Issue(line_no, str(exc)))
        if "check" in fields:
            line_no, text = fields["check"]
            try:
                check = _parse_check(_tokenize(text))
            except _LineError as exc:
                issues.append(Issue(line_no, str(exc)))
            if "do" not in fields:
                issues.append(
                    Issue(line_no, "a check needs a do: line in the same step")
                )
        hint = fields.get("if stuck", (0, ""))[1]
        parsed.append(
            Step(number, raw["purpose"], raw["why"], action, check, hint, raw["line"])
        )
    return tuple(parsed)


def _parse_done(
    entries: list[tuple[int, str]], issues: list[Issue]
) -> tuple[str, Check | None]:
    prose: list[tuple[int, str]] = []
    check = None
    for line_no, line in entries:
        field_match = _FIELD.match(line) or re.match(
            r"^\s*[-*]\s+(check)\s*:\s*(.*)$", line
        )
        if field_match and field_match.group(1).strip().lower() == "check":
            try:
                check = _parse_check(_tokenize(field_match.group(2)))
            except _LineError as exc:
                issues.append(Issue(line_no, str(exc)))
            continue
        prose.append((line_no, line))
    return _prose(prose), check


# -- validation ----------------------------------------------------------------


def _templates(skill: Skill) -> list[tuple[int, str]]:
    """Every (line, text) where a {param} may appear."""
    found: list[tuple[int, str]] = [(0, skill.goal)]
    for step in skill.steps:
        action = step.action
        if action is not None:
            found.extend((step.line, value) for _, value in action.args)
            found.extend(
                (step.line, text)
                for text in (
                    action.text,
                    action.until and action.until.value,
                    action.check and action.check.value,
                )
                if text
            )
        if step.check is not None:
            found.append((step.line, step.check.value))
    if skill.done_check is not None:
        found.append((0, skill.done_check.value))
    return found


def _validate(skill: Skill, known_tools: set[str] | None, issues: list[Issue]) -> None:
    if not skill.steps:
        issues.append(Issue(0, "a skill needs at least one step"))
    if skill.source not in SOURCES:
        issues.append(
            Issue(0, f"source '{skill.source}' is not one of: {', '.join(SOURCES)}")
        )
    if skill.state not in STATES:
        issues.append(
            Issue(0, f"state '{skill.state}' is not one of: {', '.join(STATES)}")
        )
    for tool in skill.allowed_tools:
        if not re.fullmatch(r"[a-z_]\w*", tool):
            issues.append(Issue(0, f"invalid allowed tool '{tool}'"))
        elif known_tools is not None and tool not in known_tools:
            issues.append(Issue(0, f"unknown tool '{tool}' in allowed_tools"))
    names = [item.name for item in skill.inputs]
    for name in sorted({name for name in names if names.count(name) > 1}):
        issues.append(Issue(0, f"input '{name}' is declared twice"))
    declared = set(names)
    for line, text in _templates(skill):
        for param in _PARAM.findall(text):
            if param not in declared:
                issues.append(Issue(line, f"{{{param}}} is not a declared input"))
    for step in skill.steps:
        action = step.action
        if action is None:
            continue
        if action.kind == "ask" and action.param not in declared:
            issues.append(
                Issue(
                    step.line,
                    f"ask stores into '{action.param}', which is not a declared input",
                )
            )
        if (
            action.kind == "tool"
            and known_tools is not None
            and action.tool not in known_tools
        ):
            issues.append(Issue(step.line, f"unknown tool '{action.tool}'"))


# -- public API ------------------------------------------------------------------


def parse_skill(text: str, *, known_tools: set[str] | None = None) -> Skill:
    """Parse and validate a skill file, reporting every problem at once.

    ``known_tools`` is the registry's tool names. Pass it whenever a registry
    exists: an unknown tool is the typo a person makes when editing by hand,
    and catching it at load time beats failing halfway through a run.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    issues: list[Issue] = []
    meta, body_start = _front_matter(lines, issues)

    def meta_value(key: str) -> tuple[str, int]:
        return meta.get(key, ("", 0))

    name, name_line = meta_value("name")
    description, _ = meta_value("description")
    if not name:
        issues.append(Issue(name_line or 1, "front matter needs a name"))
    if not description:
        issues.append(
            Issue(
                1, "front matter needs a description (one line: what it does, and when)"
            )
        )
    version_text, version_line = meta_value("version")
    version = 1
    if version_text:
        if version_text.isdigit() and int(version_text) >= 1:
            version = int(version_text)
        else:
            issues.append(
                Issue(version_line, f"version '{version_text}' must be a whole number")
            )

    pinned_text, pinned_line = meta_value("pinned")
    if pinned_text.lower() not in ("", "true", "false"):
        issues.append(Issue(pinned_line, "pinned must be true or false"))

    sections = _sections(lines, body_start)
    inputs = _parse_inputs(sections.get("inputs", []), issues)
    steps = _parse_steps(sections.get("steps", []), issues)
    done_when, done_check = _parse_done(sections.get("done", []), issues)
    notes_parts = [_prose(sections.get("notes", []))]
    for key, entries in sections.items():
        if key.startswith("extra:"):
            # Unknown sections are kept, not lost: a person wrote them.
            text_body = _prose(entries)
            if text_body:
                notes_parts.append(f"{key[6:]}: {text_body}")

    skill = Skill(
        name=name,
        description=description,
        steps=steps,
        phrases=tuple(
            p.strip() for p in meta_value("phrases")[0].split(";") if p.strip()
        ),
        version=version,
        source=meta_value("source")[0] or "dictated",
        goal=_prose(sections.get("goal", [])),
        inputs=inputs,
        preconditions=_bullets(sections.get("before", [])),
        done_when=done_when,
        done_check=done_check,
        never=_bullets(sections.get("never", [])),
        notes=" ".join(part for part in notes_parts if part),
        allowed_tools=tuple(
            tool.strip()
            for tool in meta_value("allowed_tools")[0].split(",")
            if tool.strip()
        ),
        state=meta_value("state")[0] or "draft",
        pinned=pinned_text.lower() == "true",
        source_run_id=meta_value("source_run_id")[0],
    )
    _validate(skill, known_tools, issues)
    if issues:
        raise SkillFormatError(sorted(issues, key=lambda issue: issue.line))
    return skill


def _quote(value: str, *, force: bool = False) -> str:
    if not force and value and _PARAM.fullmatch(value):
        return value
    if not force and value and re.fullmatch(r"[^\s\"=]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{round(seconds * 1000)}ms"
    return f"{seconds:g}s"


def _render_check(check: Check) -> str:
    if check.kind == "looks_like":
        return f"looks like {_quote(check.value)}"
    head = "window_title contains" if check.kind == "window_title" else check.kind
    text = f"{head} {_quote(check.value)}"
    if check.within is not None:
        text += f" within {_render_duration(check.within)}"
    return text


def _render_action(action: Action) -> str:
    if action.kind == "wait":
        if action.until is not None:
            return f"wait until {_render_check(action.until)}"
        return f"wait {_render_duration(action.seconds or 0)}"
    if action.kind == "ask":
        return f"ask {action.param} {_quote(action.text or '', force=True)}"
    if action.kind == "say":
        return f"say {_quote(action.text or '')}"
    if action.kind == "check":
        return f"check {_render_check(action.check)}"
    args = " ".join(f"{key}={_quote(value)}" for key, value in action.args)
    return f"{action.tool} {args}".rstrip()


def render_skill(skill: Skill) -> str:
    """Write a skill back in canonical form; ``parse_skill`` reads it back equal."""
    out = ["---", f"name: {skill.name}", f"description: {skill.description}"]
    if skill.phrases:
        out.append(f"phrases: {'; '.join(skill.phrases)}")
    out += [f"version: {skill.version}", f"source: {skill.source}"]
    if skill.allowed_tools:
        out.append(f"allowed_tools: {', '.join(skill.allowed_tools)}")
    out += [f"state: {skill.state}", f"pinned: {str(skill.pinned).lower()}"]
    if skill.source_run_id:
        out.append(f"source_run_id: {skill.source_run_id}")
    out += ["---", ""]
    if skill.goal:
        out += ["## Goal", skill.goal, ""]
    if skill.inputs:
        out.append("## Inputs")
        for item in skill.inputs:
            line = f"- {item.name} — {item.description}".rstrip()
            if item.default is not None:
                line += f" Default: {_quote(item.default) if item.default else chr(34) * 2}."
            out.append(line)
        out.append("")
    if skill.preconditions:
        out += [
            "## Before starting",
            *[f"- {item}" for item in skill.preconditions],
            "",
        ]
    out.append("## Steps")
    for step in skill.steps:
        heading = f"{step.number}. **{step.purpose}**"
        if step.why:
            heading += f" — {step.why}"
        out.append(heading)
        if step.action is not None:
            out.append(f"   - do: {_render_action(step.action)}")
        if step.check is not None:
            out.append(f"   - check: {_render_check(step.check)}")
        if step.if_stuck:
            out.append(f"   - if stuck: {step.if_stuck}")
    out.append("")
    if skill.done_when or skill.done_check:
        out.append("## Done when")
        if skill.done_when:
            out.append(skill.done_when)
        if skill.done_check is not None:
            out.append(f"- check: {_render_check(skill.done_check)}")
        out.append("")
    if skill.never:
        out += ["## Never", *[f"- {item}" for item in skill.never], ""]
    if skill.notes:
        out += ["## Notes", skill.notes, ""]
    return "\n".join(out).rstrip() + "\n"


# -- the old workflow format ---------------------------------------------------

_LEGACY_STEP_ACTION = re.compile(r"^([a-z_]\w*)\s*:\s*(.*)$")
_LEGACY_ARG = re.compile(r'([A-Za-z_]\w*)\s*=\s*("(?:[^"\\]|\\.)*"|[^,]+)')


def is_legacy(text: str) -> bool:
    """True for a file in the old workflow format.

    The old format has front matter but no ``description``, the one field every
    new skill must have, so its absence is the reliable tell.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return True
        if line.split(":", 1)[0].strip().lower() == "description":
            return False
    return False


def _legacy_action(step_text: str) -> Action | None:
    """Read an old ``tool: key="value", key=value`` step, quotes removed.

    The old runner passed quote marks straight through to the tool, so
    ``app_name="Brave"`` typed the quotes into the Start menu. This is the fix
    for files written in that format.
    """
    match = _LEGACY_STEP_ACTION.match(step_text.strip())
    if not match:
        return None
    args: list[tuple[str, str]] = []
    for key, raw in _LEGACY_ARG.findall(match.group(2)):
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = re.sub(r"\\(.)", r"\1", value[1:-1])
        args.append((key, value))
    return Action("tool", tool=match.group(1), args=tuple(args))


def _humanize(action: Action) -> str:
    words = (action.tool or "").replace("_", " ").strip()
    values = " ".join(value for _, value in action.args)
    return f"{words[:1].upper()}{words[1:]} {values}".strip()


def convert_legacy(text: str) -> Skill:
    """Convert an old workflow file into the skill format, deterministically.

    No model is involved, so conversion works even when every provider is
    busy. Tool steps become ``do:`` lines; prose steps stay plain English for
    the thinker to compile later. Nothing the boss wrote is dropped: sections
    with no direct equivalent are carried into Done when, Before starting, or
    Notes.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    issues: list[Issue] = []
    meta, body_start = _front_matter(lines, issues)

    def meta_value(key: str) -> str:
        return meta.get(key, ("", 0))[0]

    sections = _sections(lines, body_start)
    steps: list[Step] = []
    for line_no, line in sections.get("steps", []):
        start = _STEP_START.match(line)
        if not start:
            continue
        body = start.group(2).strip()
        action = _legacy_action(body)
        purpose = _humanize(action) if action is not None else body
        steps.append(Step(len(steps) + 1, purpose, action=action, line=line_no))

    notes: list[str] = []
    done: list[str] = []
    for key, entries in sections.items():
        if key == "extra:Verification":
            done.extend(_bullets(entries))
        elif key.startswith("extra:"):
            body = " ".join(item.replace("**", "") for item in _bullets(entries))
            body = body or _prose(entries)
            if body:
                notes.append(f"{key[6:]}: {body}")
    if sections.get("notes"):
        notes.insert(0, _prose(sections["notes"]))

    name = meta_value("name") or "Untitled skill"
    goal = meta_value("goal") or name
    version_text = meta_value("version")
    version = int(version_text) if version_text.isdigit() else 1
    return Skill(
        name=name,
        description=goal,
        steps=tuple(steps),
        version=version + 1,
        source="compiled",
        goal=goal,
        preconditions=_bullets(sections.get("before", [])),
        done_when=" ".join(done),
        notes=" ".join(part for part in notes if part),
        allowed_tools=tuple(
            tool.strip()
            for tool in meta_value("allowed_tools").split(",")
            if tool.strip()
        ),
    )
