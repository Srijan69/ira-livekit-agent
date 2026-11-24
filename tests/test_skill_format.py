"""The skill file format: parsing, validation and rendering.

Fixtures are inline strings rather than files so each test shows exactly the
text under test. The neutral web example exercises metadata, parameterized actions, and prose checks.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from skill_format import (
    Check,
    SkillFormatError,
    convert_legacy,
    is_legacy,
    parse_skill,
    render_skill,
)

APPROVED_EXAMPLE = """\
---
name: Open Reference Page
description: Opens a requested reference page in a browser. Not for editing or submitting forms.
phrases: open my reference page; read my reference page; open the details
version: 2
source: compiled
---

## Goal
The {page} reference is open in {browser}, and the boss can see it.

## Inputs
- page — which requested page to open. Default: "https://example.test/details".
- browser — Brave or Chrome. Default: "Brave".

## Before starting
- The browser can reach the requested site.

## Steps
1. **Open the browser** — The reference site needs a window to load in.
   - do: open_app app_name={browser}
   - check: window_title contains {browser} within 8s
   - if stuck: the Start menu can be slow; open it once more.
2. **Go to the reference site**
   - do: browse_to url="https://example.test"
3. **Let the reference site finish loading** — it takes about ten seconds on first open.
   - do: wait until page_contains "Reference Index" (max 15s)
4. **Open the details**
   - do: browse_to url={page}
   - check: looks like "details are visible"

## Done when
The requested details and page title are visible.

## Never
- Never submit a form without the requested authorization.
- Never close tabs IRA didn't open.
"""


class TestLifecycleMetadata:
    def test_metadata_round_trips_without_changing_permissions(self) -> None:
        skill = replace(
            parse_skill(APPROVED_EXAMPLE),
            allowed_tools=("open_app", "browse_to"),
            state="validated",
            pinned=True,
            source_run_id="verified-run-42",
        )
        assert parse_skill(render_skill(skill)) == skill

    def test_old_files_start_as_unpinned_drafts(self) -> None:
        skill = parse_skill(APPROVED_EXAMPLE)
        assert skill.state == "draft"
        assert skill.allowed_tools == ()
        assert skill.pinned is False
        assert skill.source_run_id == ""

    @pytest.mark.parametrize(
        "metadata", ["state: live", "pinned: yes", "allowed_tools: open_app, ../shell"]
    )
    def test_rejects_invalid_lifecycle_metadata(self, metadata: str) -> None:
        text = APPROVED_EXAMPLE.replace(
            "source: compiled", f"source: compiled\n{metadata}"
        )
        with pytest.raises(SkillFormatError):
            parse_skill(text)

    def test_legacy_conversion_retains_declared_tool_permissions(self) -> None:
        old = "---\nname: Open Notes\ngoal: Opens notes.\nallowed_tools: open_app, browse_to\n---\n## Steps\n1. open_app: app_name=Notepad\n"
        skill = convert_legacy(old)
        assert skill.allowed_tools == ("open_app", "browse_to")
        assert skill.state == "draft"


class TestApprovedExample:
    def test_reads_the_header(self) -> None:
        skill = parse_skill(APPROVED_EXAMPLE)

        assert skill.name == "Open Reference Page"
        assert skill.description.startswith("Opens a requested reference page")
        assert skill.phrases == (
            "open my reference page",
            "read my reference page",
            "open the details",
        )
        assert skill.version == 2
        assert skill.source == "compiled"

    def test_reads_goal_preconditions_done_and_never(self) -> None:
        skill = parse_skill(APPROVED_EXAMPLE)

        assert skill.goal == (
            "The {page} reference is open in {browser}, and the boss can see it."
        )
        assert skill.preconditions == ("The browser can reach the requested site.",)
        assert skill.done_when == ("The requested details and page title are visible.")
        assert len(skill.never) == 2
        assert skill.never[1] == "Never close tabs IRA didn't open."

    def test_reads_inputs_with_defaults(self) -> None:
        skill = parse_skill(APPROVED_EXAMPLE)

        page, browser = skill.inputs
        assert page.name == "page"
        assert page.description == "which requested page to open."
        assert page.default == "https://example.test/details"
        assert browser.default == "Brave"

    def test_reads_each_step_purpose_and_why(self) -> None:
        skill = parse_skill(APPROVED_EXAMPLE)

        assert [step.purpose for step in skill.steps] == [
            "Open the browser",
            "Go to the reference site",
            "Let the reference site finish loading",
            "Open the details",
        ]
        assert skill.steps[0].why == "The reference site needs a window to load in."
        assert skill.steps[1].why == ""

    def test_reads_a_tool_step_with_its_check_and_hint(self) -> None:
        step = parse_skill(APPROVED_EXAMPLE).steps[0]

        assert step.action is not None
        assert step.action.kind == "tool"
        assert step.action.tool == "open_app"
        assert step.action.args == (("app_name", "{browser}"),)
        assert step.check == Check(kind="window_title", value="{browser}", within=8.0)
        assert step.if_stuck == "the Start menu can be slow; open it once more."

    def test_quoted_values_lose_their_quotes(self) -> None:
        # The bug that motivated the new grammar: the old parser passed the
        # quote marks through, so open_app typed "Brave" with the quotes.
        step = parse_skill(APPROVED_EXAMPLE).steps[1]

        assert step.action.args == (("url", "https://example.test"),)

    def test_reads_a_wait_until_with_the_max_alias(self) -> None:
        step = parse_skill(APPROVED_EXAMPLE).steps[2]

        assert step.action.kind == "wait"
        assert step.action.until == Check(
            kind="page_contains", value="Reference Index", within=15.0
        )

    def test_reads_a_looks_like_check(self) -> None:
        step = parse_skill(APPROVED_EXAMPLE).steps[3]

        assert step.check == Check(
            kind="looks_like", value="details are visible", within=None
        )

    def test_is_fully_compiled(self) -> None:
        assert parse_skill(APPROVED_EXAMPLE).compiled is True


def _skill_with_steps(steps: str, inputs: str = "") -> str:
    return (
        "---\nname: Test Skill\ndescription: A skill for tests.\n---\n\n"
        f"## Inputs\n{inputs}\n\n## Steps\n{steps}"
    )


class TestValues:
    def test_commas_and_spaces_inside_quotes_stay_in_one_argument(self) -> None:
        # The old `key=value, key=value` splitter dropped everything after a
        # comma inside a value.
        skill = parse_skill(
            _skill_with_steps(
                "1. **Type it**\n"
                '   - do: type_into_element index=2 text="Hello, world. Again."\n'
            )
        )

        assert skill.steps[0].action.args == (
            ("index", "2"),
            ("text", "Hello, world. Again."),
        )

    def test_escaped_quotes_survive(self) -> None:
        skill = parse_skill(
            _skill_with_steps('1. **Say it**\n   - do: say "She said \\"hi\\"."\n')
        )

        assert skill.steps[0].action.text == 'She said "hi".'

    def test_parameters_inside_literals_stay_as_templates(self) -> None:
        skill = parse_skill(
            _skill_with_steps(
                '1. **Go**\n   - do: browse_to url="https://x.test/{q}"\n',
                inputs='- q — what to search. Default: "news".',
            )
        )

        assert skill.steps[0].action.args == (("url", "https://x.test/{q}"),)

    @pytest.mark.parametrize(
        ("text", "seconds"),
        [("wait 10s", 10.0), ("wait 500ms", 0.5), ("wait 1.5s", 1.5)],
    )
    def test_reads_fixed_waits(self, text: str, seconds: float) -> None:
        skill = parse_skill(_skill_with_steps(f"1. **Pause**\n   - do: {text}\n"))

        assert skill.steps[0].action.kind == "wait"
        assert skill.steps[0].action.seconds == seconds
        assert skill.steps[0].action.until is None

    def test_within_and_max_spellings_are_equivalent(self) -> None:
        within = parse_skill(
            _skill_with_steps(
                '1. **Wait**\n   - do: wait until page_contains "Ready" within 9s\n'
            )
        )
        maximum = parse_skill(
            _skill_with_steps(
                '1. **Wait**\n   - do: wait until page_contains "Ready" (max 9s)\n'
            )
        )

        assert within.steps[0].action == maximum.steps[0].action

    def test_reads_ask_say_and_check_steps(self) -> None:
        skill = parse_skill(
            _skill_with_steps(
                "1. **Ask what broke**\n"
                '   - do: ask problem "What went wrong, boss?"\n'
                "2. **Confirm**\n"
                '   - do: say "Logging it now."\n'
                "3. **Make sure the editor is up**\n"
                "   - do: check window_title contains Notepad within 5s\n",
                inputs="- problem — what went wrong.",
            )
        )

        ask, say, check = (step.action for step in skill.steps)
        assert (ask.kind, ask.param, ask.text) == (
            "ask",
            "problem",
            "What went wrong, boss?",
        )
        assert (say.kind, say.text) == ("say", "Logging it now.")
        assert check.kind == "check"
        assert check.check == Check("window_title", "Notepad", 5.0)

    def test_reads_page_element_checks(self) -> None:
        skill = parse_skill(
            _skill_with_steps(
                '1. **Find play**\n   - do: wait until page_element "Pause" within 6s\n'
            )
        )

        assert skill.steps[0].action.until == Check("page_element", "Pause", 6.0)

    def test_an_input_without_a_default_is_required(self) -> None:
        skill = parse_skill(
            _skill_with_steps(
                '1. **Go**\n   - do: say "{who}"\n',
                inputs="- who — who to greet.",
            )
        )

        assert skill.inputs[0].default is None
        assert skill.inputs[0].required is True


class TestUncompiledSteps:
    def test_a_step_without_do_is_kept_as_plain_english(self) -> None:
        # The boss can write or dictate a skill in plain English; the thinker
        # fills in the do: lines later. The parser must accept that, not reject
        # it as malformed.
        skill = parse_skill(
            _skill_with_steps("1. **Open my notes app**\n2. **Write today's date**\n")
        )

        assert [step.action for step in skill.steps] == [None, None]
        assert skill.compiled is False


def _errors(text: str, **kwargs) -> list[str]:
    with pytest.raises(SkillFormatError) as caught:
        parse_skill(text, **kwargs)
    return [str(issue) for issue in caught.value.issues]


class TestValidation:
    """A broken file never half-runs: it is rejected with the exact problem."""

    def test_names_an_unknown_tool_and_its_line(self) -> None:
        errors = _errors(
            _skill_with_steps("1. **Open it**\n   - do: open_ap app_name=Brave\n"),
            known_tools={"open_app"},
        )

        assert errors == ["line 10: unknown tool 'open_ap'"]

    def test_rejects_a_parameter_that_was_never_declared(self) -> None:
        errors = _errors(_skill_with_steps('1. **Go**\n   - do: say "{nobody}"\n'))

        assert any("{nobody} is not a declared input" in error for error in errors)

    def test_requires_a_description(self) -> None:
        errors = _errors("---\nname: X\n---\n\n## Steps\n1. **Do it**\n")

        assert any("needs a description" in error for error in errors)

    def test_rejects_a_duration_it_cannot_read(self) -> None:
        errors = _errors(_skill_with_steps("1. **Pause**\n   - do: wait 10 seconds\n"))

        assert any("wait 10s" in error for error in errors)

    def test_rejects_an_unknown_check_kind(self) -> None:
        errors = _errors(
            _skill_with_steps(
                "1. **Open**\n   - do: open_app app_name=X\n"
                '   - check: smells_like "coffee"\n'
            )
        )

        assert any("unknown check 'smells_like'" in error for error in errors)

    def test_rejects_an_unknown_step_field(self) -> None:
        errors = _errors(
            _skill_with_steps(
                "1. **Open**\n   - do: open_app app_name=X\n   - maybe: later\n"
            )
        )

        assert errors == [
            "line 12: unknown step field 'maybe' (expected do, check, if stuck)"
        ]

    def test_reports_every_problem_at_once(self) -> None:
        # One round trip to fix a file, not one per typo.
        errors = _errors(
            _skill_with_steps(
                "1. **Open**\n   - do: open_ap app_name=X\n"
                "2. **Pause**\n   - do: wait forever\n"
            ),
            known_tools={"open_app"},
        )

        assert len(errors) == 2

    def test_rejects_ask_into_an_undeclared_input(self) -> None:
        errors = _errors(
            _skill_with_steps('1. **Ask**\n   - do: ask colour "Which colour?"\n')
        )

        assert any("ask stores into 'colour'" in error for error in errors)

    def test_rejects_a_check_without_an_action(self) -> None:
        errors = _errors(
            _skill_with_steps('1. **Look**\n   - check: page_contains "Hi"\n')
        )

        assert any("needs a do: line" in error for error in errors)

    def test_rejects_an_unterminated_quote(self) -> None:
        errors = _errors(_skill_with_steps('1. **Say**\n   - do: say "hello\n'))

        assert any("missing its closing quote" in error for error in errors)

    def test_rejects_a_positional_tool_argument(self) -> None:
        errors = _errors(_skill_with_steps("1. **Open**\n   - do: open_app Brave\n"))

        assert any("tool arguments read key=value" in error for error in errors)


class TestRendering:
    def test_round_trips_the_approved_example(self) -> None:
        skill = parse_skill(APPROVED_EXAMPLE)

        assert parse_skill(render_skill(skill)) == skill

    def test_writes_the_canonical_within_spelling(self) -> None:
        rendered = render_skill(parse_skill(APPROVED_EXAMPLE))

        assert 'wait until page_contains "Reference Index" within 15s' in rendered
        assert "(max" not in rendered

    def test_quotes_values_that_need_it(self) -> None:
        skill = parse_skill(
            _skill_with_steps(
                '1. **Type**\n   - do: type_into_element index=2 text="Say \\"hi\\", now"\n'
            )
        )

        rendered = render_skill(skill)

        assert 'index=2 text="Say \\"hi\\", now"' in rendered
        assert parse_skill(rendered) == skill

    def test_round_trips_every_step_kind_and_plain_english_steps(self) -> None:
        text = (
            "---\nname: Everything\ndescription: Uses every kind of step.\n"
            "phrases: do everything\nversion: 3\nsource: corrected\n---\n\n"
            "## Goal\nEvery kind of step runs.\n\n"
            '## Inputs\n- who — who to greet.\n- where — where to go. Default: "home".\n\n'
            "## Before starting\n- The boss is at the desk.\n\n"
            "## Steps\n"
            '1. **Ask who** — we need a name.\n   - do: ask who "Who is it?"\n'
            '2. **Greet**\n   - do: say "Hello {who}."\n'
            "3. **Pause briefly**\n   - do: wait 500ms\n"
            "4. **Make sure the editor is up**\n"
            "   - do: check window_title contains Notepad within 5s\n"
            '5. **Open a page**\n   - do: browse_to url="https://x.test/{where}"\n'
            '   - check: page_element "Search" within 3s\n'
            "   - if stuck: reload once.\n"
            "6. **Write today's summary**\n\n"
            '## Done when\nThe page is open.\n- check: page_contains "Welcome"\n\n'
            "## Never\n- Never submit a form.\n\n"
            "## Notes\nKept for tests.\n"
        )
        skill = parse_skill(text)

        assert parse_skill(render_skill(skill)) == skill
        assert skill.done_check == Check("page_contains", "Welcome", None)
        assert skill.compiled is False


# Legacy web and error-log fixtures retain the original format grammar.
LEGACY_WEB_PAGE = """\
---
name: Open Reference Page
goal: Open the requested reference page in Brave browser.
version: 1
worker: lightweight
linear: true
requires_screen: false
allowed_tools: open_app, browse_to
failure_policy: stop
---

# Open Reference Page

Open the requested reference page in Brave browser.

## Steps

1. open_app: app_name="Brave"
2. browse_to: url="https://example.test"
3. browse_to: url="https://example.test/details"

## Wait

- **Wait for the reference site to load in the browser for around 10sec**

## Verification

- Check the reference URL https://example.test/details in "Reference Index" and open it.
- Confirm details are visible.
"""

LEGACY_LOG_ERROR = """\
---
name: Log Error
goal: Log Error
---

# Log Error

Log Error

## Steps

1. Identify the problem or error
2. Create a new Markdown file
3. Write the problem description and context into the file
"""


class TestLegacyConversion:
    def test_recognises_old_files(self) -> None:
        assert is_legacy(LEGACY_WEB_PAGE)
        assert is_legacy(LEGACY_LOG_ERROR)
        assert not is_legacy(APPROVED_EXAMPLE)

    def test_converts_tool_steps_without_the_quote_bug(self) -> None:
        skill = convert_legacy(LEGACY_WEB_PAGE)

        assert [step.action.args for step in skill.steps] == [
            (("app_name", "Brave"),),
            (("url", "https://example.test"),),
            (("url", "https://example.test/details"),),
        ]
        assert skill.compiled is True

    def test_keeps_the_meaning_of_every_old_section(self) -> None:
        # Nothing the boss wrote is dropped: the wait note and the
        # verification lines survive for the thinker to act on.
        skill = convert_legacy(LEGACY_WEB_PAGE)

        assert skill.description == (
            "Open the requested reference page in Brave browser."
        )
        assert "Confirm details are visible." in skill.done_when
        assert "Wait for the reference site to load" in skill.notes

    def test_marks_the_conversion_as_a_new_compiled_version(self) -> None:
        skill = convert_legacy(LEGACY_WEB_PAGE)

        assert (skill.version, skill.source) == (2, "compiled")

    def test_gives_converted_steps_a_readable_purpose(self) -> None:
        skill = convert_legacy(LEGACY_WEB_PAGE)

        assert skill.steps[0].purpose == "Open app Brave"

    def test_plain_english_steps_stay_plain_english(self) -> None:
        skill = convert_legacy(LEGACY_LOG_ERROR)

        assert [step.purpose for step in skill.steps] == [
            "Identify the problem or error",
            "Create a new Markdown file",
            "Write the problem description and context into the file",
        ]
        assert skill.compiled is False

    @pytest.mark.parametrize("legacy", [LEGACY_WEB_PAGE, LEGACY_LOG_ERROR])
    def test_conversion_produces_a_valid_new_format_file(self, legacy: str) -> None:
        skill = convert_legacy(legacy)

        assert parse_skill(render_skill(skill)) == skill
