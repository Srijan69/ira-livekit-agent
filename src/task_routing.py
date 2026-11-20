"""Small deterministic task contracts shared by skill routing and admission.

Unknown meaning is left to the semantic judge. A matching action word alone
never establishes authority, and a routing decision is not an action permit.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qs, unquote, urlsplit

_SEARCH_DOMAINS = frozenset(
    {
        "bing.com",
        "google.com",
        "google.co.in",
        "duckduckgo.com",
        "search.brave.com",
        "search.yahoo.com",
        "ecosia.org",
        "wikipedia.org",
    }
)


def normalized_name(value):
    return " ".join(re.sub(r"[-_]", " ", str(value).casefold()).split())


_COMMAND_START = r"(?:open|launch|start|visit|go to|navigate to|browse to|play|pause|resume|skip|next|previous|shuffle|press|click)\b"


def _split_commands(text):
    """Separate explicit action clauses while preserving quoted subject text."""
    quoted = set()
    closing = None
    pairs = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}
    for index, char in enumerate(text):
        if closing is not None:
            quoted.add(index)
        if index and text[index - 1] == "\\":
            continue
        if (
            char in {"'", "\u2019"}
            and index
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        ):
            continue
        if char == closing:
            closing = None
        elif closing is None and char in pairs:
            closing = pairs[char]
    separators = re.finditer(
        r"(?:\s+(?:and(?:\s+then)?|then)\s+|[,;]\s*(?:then\s+)?)(?="
        + r"(?:(?:do not|don't|never|avoid)\s+['\"]?)?"
        + _COMMAND_START
        + ")",
        text,
        re.I,
    )
    parts = []
    start = 0
    for match in separators:
        if match.start() not in quoted:
            parts.append(text[start : match.start()].strip())
            start = match.end()
    return (*parts, text[start:].strip())


def _request_clauses(text):
    clauses = _split_commands(text)
    first = clauses[0]
    if research_subject(first) or re.match(
        r"^(?:please\s+)?" + _COMMAND_START, first, re.I
    ):
        return clauses
    return (text,)


def _navigation_target(clause):
    match = re.fullmatch(
        r"(?:please\s+)?(?:open|visit|go to|navigate to|browse to)\s+(.+)", clause, re.I
    )
    if match:
        target = match[1].strip().strip("'\"\u201c\u201d")
        return target if _url(target) else ""
    return ""


def _media_control(name, args):
    if "key" in name:
        keys = ("key", "keys")
    elif "click" in name:
        labels = [str(args.get(key, "")) for key in ("label", "action")]
        labels.extend(
            re.findall(
                r"(?:aria-label|title|text)\s*=\s*['\"]([^'\"]+)['\"]",
                str(args.get("selector", "")),
                re.I,
            )
        )
        controls = {
            "play",
            "pause",
            "resume",
            "shuffle",
            "like",
            "next",
            "previous",
            "next track",
            "previous track",
        }
        return " ".join(
            label for label in labels if label.strip().casefold() in controls
        )
    elif "control" in name or name.startswith("spotify_"):
        keys = ("action",)
    else:
        return ""
    return " ".join(str(args.get(key, "")) for key in keys)


def research_subject(text):
    lead = r"^\s*(?:(?:please|can you|could you|would you)\s+)?(?:(?:do|perform|give me)\s+(?:a\s+)?)?"
    match = re.match(
        lead
        + r"(?:run|use|follow|execute|try)\s+research[ -]topic\s+(?:about|on|for)\s+(.+)",
        text,
        re.I,
    )
    if not match:
        match = re.match(
            lead
            + r"(?:research|(?:deep\s+)?search|look\s+up|investigate)\s+(?:(?:about|on|for|into)\s+)?(.+)",
            text,
            re.I,
        )
    if not match:
        match = re.match(
            lead
            + r"(?:gather|find|collect)\s+(?:(?:all|recent|current)\s+)?(?:the\s+)?information\s+(?:about|on)\s+(.+)",
            text,
            re.I,
        )
    if not match:
        return ""
    subject = re.split(
        r"[;,]|\s+(?:using|use (?:your|all|the)\b|with|from|as a company|and (?:save|send|open)|in (?:Chrome|Brave|Edge))\b",
        _split_commands(match[1])[0],
        maxsplit=1,
        flags=re.I,
    )[0]
    subject = re.sub(r"\s+company$", "", subject, flags=re.I)
    return subject.strip(" .?!\"'")


def task_kind(text):
    if research_subject(text):
        return "web_research"
    if re.match(r"\s*(?:please\s+)?(?:open|launch|start)\s+", text, re.I):
        return (
            "navigate"
            if re.search(r"https?://|\b(?:website|web page|url)\b", text, re.I)
            else "open_app"
        )
    if re.match(r"\s*(?:please\s+)?(?:close|quit)\s+", text, re.I):
        return "close_window"
    if re.search(r"\b(?:send|message)\b.*\b(?:WhatsApp|message)\b", text, re.I):
        return "send_message"
    if re.search(r"\b(?:fill|type|enter)\b", text, re.I):
        return "fill_field"
    return "unknown"


def _url(value):
    try:
        value = str(value).strip()
        if re.search(r"\s", value):
            return None
        parsed = urlsplit(value)
        if not parsed.scheme:
            if not re.fullmatch(
                r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}(?:[/?#][^\s]*)?", value
            ):
                return None
            parsed = urlsplit("https://" + value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
        ):
            return None
        _ = parsed.port
        return parsed
    except ValueError:
        return None


def _same_navigation(first, second):
    left, right = _url(first), _url(second)
    return (
        left is not None
        and right is not None
        and (left.scheme, left.netloc.casefold(), left.path, left.query, left.fragment)
        == (
            right.scheme,
            right.netloc.casefold(),
            right.path,
            right.query,
            right.fragment,
        )
    )


def _subject_matches(subject, value):
    tokens = re.findall(r"[a-z0-9]+", re.sub(r"['\u2019]s\b", "", subject.casefold()))
    words = set(re.findall(r"[a-z0-9]+", unquote(str(value)).casefold()))
    return bool(tokens) and all(token in words for token in tokens)


def _research_url(subject, value):
    parsed = _url(value)
    if parsed is None:
        return False
    query = parse_qs(parsed.query)
    search = next(
        (
            query[key][0]
            for key in ("q", "query", "search", "search_query")
            if key in query
        ),
        "",
    )
    if search:
        host = parsed.hostname.casefold()
        known_search = any(
            host == domain or host.endswith("." + domain) for domain in _SEARCH_DOMAINS
        )
        primary = re.sub(r"[^a-z0-9]", "", subject.casefold()) in host.split(".")
        return (known_search or primary) and _subject_matches(subject, search)
    # A subject's exact domain is a useful primary source; arbitrary path
    # words and domain substrings do not establish a source relationship.
    subject_host = re.sub(r"[^a-z0-9]", "", subject.casefold())
    return subject_host in parsed.hostname.casefold().split(".")


def _observed_source(subject, target, observation, history):
    for item in (observation or {}, *history):
        if not isinstance(item, dict) and not hasattr(item, "get"):
            continue
        current = (
            item.get("url")
            or item.get("page_url")
            or item.get("arguments", {}).get("url", "")
        )
        if not _research_url(subject, current):
            continue
        links = item.get("links", item.get("source_links", ()))
        for link in links:
            candidate = (
                link.get("url", link.get("href")) if isinstance(link, dict) else link
            )
            if candidate == target:
                return True
    return False


def classify_action(
    request_text, name, args, *, observation=None, history=(), answers=()
):
    """Return established task fit, or None when interpretation is unresolved."""
    if (
        not isinstance(request_text, str)
        or not request_text.strip()
        or (not isinstance(args, dict) and not hasattr(args, "get"))
    ):
        return None
    text = request_text.strip()
    subject = research_subject(text)
    url = args.get("url", "")
    clauses = _request_clauses(text)
    requested_urls = [_navigation_target(clause) for clause in clauses]
    control = _media_control(name, args)
    for clause in clauses:
        excluded = re.fullmatch(
            r"(?:please\s+)?(?:do not|don't|never|avoid)\s+(.+)", clause, re.I
        )
        if excluded is None:
            continue
        command = excluded[1].strip().strip("'\"")
        if (
            name in {"browse_to", "new_tab"}
            and url
            and _same_navigation(_navigation_target(command), url)
        ):
            return "deny", "The human excluded this exact destination."
        if name == "open_app":
            excluded_app = re.fullmatch(r"(?:open|launch|start)\s+(.+)", command, re.I)
            if excluded_app and normalized_name(excluded_app[1]) == normalized_name(
                args.get("app_name", "")
            ):
                return "deny", "The human excluded this exact application."
    for clause in clauses[1:]:
        if name == "open_app":
            explicit_app = re.fullmatch(
                r"(?:please\s+)?(?:open|launch|start)\s+(.+?)[.!]?", clause, re.I
            )
            if explicit_app and normalized_name(
                args.get("app_name", "")
            ) == normalized_name(explicit_app[1].strip("'\"")):
                return (
                    "allow",
                    "The human requested this exact application in the compound task.",
                )
        if name in {"press_desktop_key", "press_key"}:
            explicit_key = re.fullmatch(r"(?:please\s+)?press\s+(.+)", clause, re.I)
            if explicit_key and normalized_name(args.get("key", "")) == normalized_name(
                explicit_key[1]
            ):
                return (
                    "allow",
                    "The human requested this exact key in the compound task.",
                )
        explicit_media = re.match(
            r"(?:please\s+)?(play|pause|resume|skip|next|previous|shuffle)\b",
            clause,
            re.I,
        )
        if explicit_media and control:
            expected = {"resume": "play", "skip": "next"}.get(
                explicit_media[1].casefold(), explicit_media[1].casefold()
            )
            if expected in re.findall(r"[a-z]+", normalized_name(control)):
                return None
    if name in {"browse_to", "new_tab"} and url:
        if any(_same_navigation(url, value) for value in requested_urls):
            return "allow", "The human requested this exact destination."
        if subject:
            if _url(subject) and _same_navigation(url, subject):
                return "allow", "The human requested researching this exact URL."
            if _observed_source(subject, url, observation, history):
                return (
                    "allow",
                    "Navigation serves the requested research subject or an observed source.",
                )
            parsed = _url(url)
            if parsed is None:
                return "deny", "This destination cannot serve web research."
            if _research_url(subject, url):
                return "allow", "Navigation serves the requested research subject."
            # Media player navigation has a concrete unrelated purpose. This
            # remains researchable when the human actually names that subject.
            media_host = re.search(
                r"\b(?:spotify|music|playlist|player)\b", parsed.hostname, re.I
            )
            if media_host and media_host[0].casefold() not in re.findall(
                r"[a-z0-9]+", subject.casefold()
            ):
                return "deny", "A media destination is unrelated to the research goal."
            queries = parse_qs(parsed.query)
            search = next(
                (
                    queries[key][0]
                    for key in ("q", "query", "search", "search_query")
                    if key in queries
                ),
                "",
            )
            if search and not _subject_matches(subject, search):
                return "deny", "The search query does not concern the current subject."
        return None
    if name == "open_app":
        proposed = normalized_name(args.get("app_name", ""))
        exact = re.fullmatch(
            r"(?:please\s+)?(?:open|launch|start)\s+(.+?)[.!]?", text, re.I
        )
        if exact and proposed == normalized_name(exact[1]).removesuffix("."):
            return "allow", "The human requested this exact application."
        if subject:
            if proposed in {
                "chrome",
                "google chrome",
                "brave",
                "edge",
                "microsoft edge",
                "firefox",
            }:
                return (
                    "allow",
                    "A browser is a necessary means for the requested research.",
                )
            return "deny", "Opening this application does not serve the research goal."
        return None
    if subject:
        if name in {
            "read_page",
            "list_page_elements",
            "list_tabs",
            "capture_screen",
            "inspect_screen",
            "go_back",
        }:
            return "allow", "Observation or navigation supports current research."
        if name == "save_note" and re.search(
            r"\b(?:and|then)\s+(?:save|write)\s+(?:(?:the|a)\s+)?(?:summary|report|findings)\b",
            text,
            re.I,
        ):
            return (
                ("allow", "The human requested saving this subject's research summary.")
                if _subject_matches(subject, args.get("text", args.get("note", "")))
                else (
                    "deny",
                    "The saved content does not concern the requested subject.",
                )
            )
        if name in {
            "remember",
            "send_message",
            "create_styled_spreadsheet",
            "generate_market_report",
            "optimize_image_assets",
            "resize_image_file",
        } and re.search(
            r"\b(?:and|then)\s+(?:save|write|send|remember|create|generate|resize)\b",
            text,
            re.I,
        ):
            return None
        if name in {
            "save_note",
            "remember",
            "send_message",
            "create_styled_spreadsheet",
            "generate_market_report",
            "optimize_image_assets",
            "resize_image_file",
        } or name.startswith("spotify_"):
            return "deny", "This effect was not requested as part of research."
        if re.search(
            r"\b(?:media[_ ](?:play|pause|next|prev)\w*|play|pause|playlist|like|shuffle)\b",
            control,
            re.I,
        ):
            return "deny", "Media control is unrelated to the research goal."
        if name == "switch_tab" and observation:
            tabs = observation.get("tabs", ())
            target = args.get("tab_id", args.get("index"))
            for tab in tabs:
                if str(tab.get("id", tab.get("index"))) == str(target):
                    return classify_action(
                        text,
                        "browse_to",
                        {"url": tab.get("url", "")},
                        observation=observation,
                        history=history,
                    )
    return None


@dataclass(frozen=True)
class BoundInput:
    name: str
    value: str | int | bool
    source: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z_]\w*", self.name):
            raise ValueError("Invalid bound input name")
        if self.source not in {"request", "answer", "observation", "default"}:
            raise ValueError(
                "A bound input requires current request, answer, observation, or default provenance"
            )
        if not isinstance(self.value, (str, int, bool)):
            raise ValueError("Unsupported bound input value type")


@dataclass(frozen=True)
class TaskDecision:
    request_id: str
    task_kind: str
    mode: str
    reason: str
    selected_id: str = ""
    document_digest: str = ""
    version: str = ""
    inputs: tuple[BoundInput, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_effects: tuple[str, ...] = ()
    done_when: tuple[str, ...] = ()

    def __post_init__(self):
        for field in ("inputs", "allowed_tools", "allowed_effects", "done_when"):
            object.__setattr__(self, field, tuple(getattr(self, field)))
        if not self.request_id or self.mode not in {
            "direct_tool",
            "procedure",
            "workflow",
            "clarify",
            "no_match",
        }:
            raise ValueError("Invalid task decision identity or mode")
        if self.selected_id and (
            not self.selected_id.startswith(("procedure:", "workflow:"))
            or not re.fullmatch(r"[a-f0-9]{64}", self.document_digest)
        ):
            raise ValueError(
                "A selected decision requires canonical identity and digest"
            )
        if self.selected_id:
            namespace, identifier = self.selected_id.split(":", 1)
            parts = identifier.split("/")
            if (
                any(part in {"", ".", ".."} for part in parts)
                or "\\" in identifier
                or ":" in identifier
                or (namespace == "workflow" and len(parts) != 1)
            ):
                raise ValueError(
                    "A selected decision requires a canonical contained identity"
                )
        if len({item.name for item in self.inputs}) != len(self.inputs):
            raise ValueError("Duplicate bound inputs")

    @property
    def input_values(self):
        return MappingProxyType({item.name: item.value for item in self.inputs})

    def record(self):
        return {
            "request_id": self.request_id,
            "task_kind": self.task_kind,
            "mode": self.mode,
            "reason": self.reason,
            "selected_id": self.selected_id,
            "document_digest": self.document_digest,
            "version": self.version,
            "inputs": {
                item.name: {"source": item.source, "value": "[redacted]"}
                for item in self.inputs
            },
            "allowed_tools": list(self.allowed_tools),
            "allowed_effects": list(self.allowed_effects),
            "done_when": list(self.done_when),
        }

    @property
    def digest(self):
        return hashlib.sha256(
            json.dumps(self.record(), sort_keys=True).encode()
        ).hexdigest()

    def validate_current(self, request, catalog, store=None):
        if request is None or request.id != self.request_id:
            return False
        if self.selected_id.startswith("procedure:"):
            current = catalog.get_skill(self.selected_id)
            lifecycle = catalog.state_store.state(current) if current else {}
            return (
                current is not None
                and current.digest == self.document_digest
                and not lifecycle["archived"]
                and lifecycle["state"] in {"validated", "enabled"}
            )
        if self.selected_id.startswith("workflow:") and store:
            from skill_format import render_skill

            current = store.get(self.selected_id.removeprefix("workflow:"))
            return (
                current is not None
                and hashlib.sha256(render_skill(current).encode()).hexdigest()
                == self.document_digest
            )
        return not self.selected_id


def _bind_inputs(info, request, provided, answers):
    routing = info.routing
    declared = routing.get("inputs", {})
    if not isinstance(declared, dict) or set(provided) - set(declared):
        raise ValueError("Unknown procedure input names; clarify the requested inputs.")
    subject = research_subject(request.text)
    result = []
    for name, schema in declared.items():
        schema = schema if isinstance(schema, dict) else {"required": bool(schema)}
        natural = subject if name == "subject" and subject else None
        if name in {"app_name", "app", "application"}:
            exact = re.fullmatch(
                r"(?:please\s+)?(?:open|launch|start)\s+(.+?)[.!]?",
                request.text.strip(),
                re.I,
            )
            if exact and not _url(exact[1]):
                natural = exact[1]
        if name in {"url", "target_url"}:
            urls = re.findall(r"https?://[^\s<>\"']+", request.text)
            if len(urls) == 1:
                natural = urls[0]
        if name in provided:
            value = provided[name]
            if natural is not None and normalized_name(value) != normalized_name(
                natural
            ):
                raise ValueError(
                    "The provided input conflicts with the current human target."
                )
            source = (
                "request"
                if natural is not None
                or str(value).casefold() in request.text.casefold()
                else "answer"
                if str(value) in answers
                else ""
            )
            if not source:
                raise ValueError(
                    "An input needs current human request or explicit answer provenance."
                )
        elif natural is not None:
            value, source = natural, "request"
        elif name == "depth" and re.search(
            r"\b(?:brief|quick|shallow)\b", request.text, re.I
        ):
            value, source = "brief", "request"
        elif "default" in schema:
            value, source = schema["default"], "default"
        elif schema.get("required", True):
            raise ValueError("Missing required input: " + name)
        else:
            continue
        expected = schema.get("type", "string")
        if (
            (expected == "string" and (not isinstance(value, str) or not value.strip()))
            or (
                expected == "integer"
                and (not isinstance(value, int) or isinstance(value, bool))
            )
            or (expected == "boolean" and not isinstance(value, bool))
        ):
            raise ValueError("Invalid input type for " + name)
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("Unsupported input choice for " + name)
        result.append(BoundInput(name, value, source))
    return tuple(result)


def decide_task(
    request,
    catalog,
    store=None,
    *,
    available_tools=(),
    selected=None,
    explicit=False,
    inputs=None,
    answers=(),
):
    """Narrow candidates; explicit run-once validates without enabling reuse."""
    if request is None or not getattr(request, "id", ""):
        raise ValueError("A finalized current request is required for routing")
    kind = task_kind(request.text)
    provided = dict(inputs or {})
    available = set(available_tools)
    base = {"request_id": request.id, "task_kind": kind}
    if store and ((selected and selected.startswith("workflow:")) or not selected):
        workflow = (
            store.find_by_phrase(selected.removeprefix("workflow:"))
            if selected
            else store.find_by_phrase(request.text)
        )
        if workflow is not None:
            from types import SimpleNamespace

            from skill_format import render_skill

            entries = store.list()
            entry = next(
                item
                for item in entries
                if item.skill and item.skill.name == workflow.name
            )
            human_named = explicit and normalized_name(
                workflow.name
            ) in normalized_name(request.text)
            if workflow.state != "enabled" and not human_named:
                return TaskDecision(
                    **base,
                    mode="no_match",
                    reason="Automatic workflow selection requires enabled state.",
                )
            if available and set(workflow.allowed_tools) - available:
                return TaskDecision(
                    **base,
                    mode="no_match",
                    reason="Required workflow tools are unavailable.",
                )
            try:
                proxy = SimpleNamespace(
                    routing={
                        "inputs": {
                            item.name: {
                                "required": False,
                                "type": "string",
                                **(
                                    {"default": item.default}
                                    if item.default is not None
                                    else {}
                                ),
                            }
                            for item in workflow.inputs
                        }
                    }
                )
                bound = _bind_inputs(
                    proxy,
                    request,
                    provided,
                    tuple(getattr(request, "answers", ())) + tuple(answers),
                )
            except ValueError as exc:
                return TaskDecision(**base, mode="clarify", reason=str(exc))
            values = {item.name: str(item.value) for item in bound}
            for step in workflow.steps:
                if step.action is None or step.action.kind != "tool":
                    continue
                arguments = {
                    key: re.sub(
                        r"\{(\w+)\}",
                        lambda match: values.get(match[1], match[0]),
                        value,
                    )
                    for key, value in step.action.args
                }
                fit = classify_action(request.text, step.action.tool, arguments)
                if (fit and fit[0] == "deny") or (
                    not fit and kind == "web_research" and not human_named
                ):
                    return TaskDecision(
                        **base,
                        mode="no_match",
                        reason="Workflow actions do not fit the current task.",
                    )
            return TaskDecision(
                **base,
                mode="workflow",
                reason="Explicit current-task workflow invocation."
                if human_named
                else "Exact reviewed workflow phrase matched.",
                selected_id="workflow:" + entry.slug,
                document_digest=hashlib.sha256(
                    render_skill(workflow).encode()
                ).hexdigest(),
                version=str(workflow.version),
                inputs=bound,
                allowed_tools=workflow.allowed_tools,
                allowed_effects=("task_scoped",),
                done_when=("compiled_checks",),
            )
    candidates = (
        [catalog.get_skill(selected)]
        if selected and not selected.startswith("workflow:")
        else []
    )
    if not selected:
        candidates = [
            info
            for info in catalog.list_skills()
            if kind in info.routing.get("task_kinds", ())
            and catalog.state_store.eligible(info)
        ]
    candidates = [info for info in candidates if info is not None]
    if len(candidates) > 1:
        return TaskDecision(
            **base,
            mode="clarify",
            reason="Several reviewed procedures fit; choose the intended procedure.",
        )
    if candidates:
        info = candidates[0]
        state = catalog.state_store.state(info)
        if state["archived"]:
            return TaskDecision(
                **base, mode="no_match", reason="The requested procedure is archived."
            )
        human_named = (
            normalized_name(info.name) in normalized_name(request.text)
            if explicit
            else False
        )
        compatible = kind in info.routing.get("task_kinds", ())
        if not compatible and not human_named:
            return TaskDecision(
                **base,
                mode="no_match",
                reason="The procedure does not fit the current requested task.",
            )
        if (
            any(
                re.search(
                    r"(?<!\w)" + re.escape(str(exclusion)) + r"(?!\w)",
                    request.text,
                    re.I,
                )
                for exclusion in info.routing.get("exclusions", ())
            )
            and not human_named
        ):
            return TaskDecision(
                **base,
                mode="no_match",
                reason="The current request is excluded by this procedure's applicability.",
            )
        try:
            if explicit:
                catalog.state_store.validate(info, available_tools=available)
            elif state["state"] != "enabled":
                raise ValueError(
                    "Automatic procedure selection requires reviewed enabled state."
                )
            if available and set(info.routing.get("allowed_tools", ())) - available:
                raise ValueError("Required procedure tools are unavailable.")
            bound = _bind_inputs(
                info,
                request,
                provided,
                tuple(getattr(request, "answers", ())) + tuple(answers),
            )
        except ValueError as exc:
            return TaskDecision(**base, mode="clarify", reason=str(exc))
        return TaskDecision(
            **base,
            mode="procedure",
            reason="Explicit current-task procedure invocation."
            if explicit
            else "Reviewed procedure fits the current task.",
            selected_id=info.canonical_id,
            document_digest=info.digest,
            version=info.version,
            inputs=bound,
            allowed_tools=tuple(info.routing["allowed_tools"]),
            allowed_effects=tuple(info.routing["allowed_effects"]),
            done_when=tuple(info.routing["done_when"]),
        )
    if selected:
        return TaskDecision(
            **base,
            mode="no_match",
            reason="No unambiguous current procedure matches that exact name.",
        )
    direct = kind == "web_research" and {"browse_to", "read_page"}.issubset(available)
    return TaskDecision(
        **base,
        mode="direct_tool" if direct else "no_match",
        reason="Use ordinary browser tools for this research; no eligible procedure matched."
        if direct
        else "No relevant supported procedure matched the current request.",
    )
