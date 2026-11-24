"""Behavioral regressions for procedural loading and generic browser safety."""

import json

import pytest

from agent import Assistant
from browser import _launch_options
from dual_memory import DualMemoryStore
from skill_store import SkillStore
from skills import SkillCatalog
from tests.fixtures.playwright_fakes import FakeContext, FakePage, controller_with
from tool_dispatch import repair_json_string


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")


def assistant(tmp_path, **kwargs):
    catalog = SkillCatalog(tmp_path / "skills")
    catalog.create_skill(
        "open-example",
        """---
name: open-example
description: Inspect the requested web page.
metadata:
  ira:
    task_kinds: [web_navigation]
    inputs:
      url: {required: true, type: string}
    allowed_tools: [browse_to, read_page]
    allowed_effects: [navigate, read]
    done_when: [requested_page_read]
---
Use browse_to url={url} and read_page to inspect the requested page.
""",
        category="web",
    )
    return Assistant(
        skills_catalog=catalog,
        skills=SkillStore(tmp_path / "workflows"),
        dual_memory=DualMemoryStore(tmp_path / "memory"),
        **kwargs,
    )


async def test_advertised_procedure_is_loadable_through_public_list_view_run(tmp_path):
    agent = assistant(tmp_path)
    assert "open-example" in await agent.list_skills(None)
    assert "browse_to" in await agent.view_skill(None, "open-example")
    agent._request_scope.begin_request(
        "Run open-example for https://example.test", source_id="human:procedure-load"
    )
    output = await agent.run_skill(
        None, "open-example", '{"url":"https://example.test"}'
    )
    assert "browse_to" in output
    assert "procedure_ready" in output
    assert "completed" not in output.lower()
    assert agent._skill_service is None  # A procedure is followed by the model.


def test_default_compiled_store_is_distinct_from_procedure_catalog(tmp_path):
    catalog = SkillCatalog(tmp_path / "skills")
    agent = Assistant(
        skills_catalog=catalog, dual_memory=DualMemoryStore(tmp_path / "memory")
    )
    assert isinstance(agent._skills, SkillStore)
    assert agent._skills is not catalog


def test_valid_json_and_nested_payload_are_preserved_byte_for_byte():
    raw = '{"message":"Keep comma, ] and comma, } and escaped \\"quote\\"", "inputs_json":"{\\"text\\":\\"x, ]\\"}"}'
    assert json.loads(repair_json_string(raw)) == json.loads(raw)


def test_trailing_comma_repair_preserves_strings():
    assert json.loads(repair_json_string('{"message":"x, ]", "items":[1,2,],}')) == {
        "message": "x, ]",
        "items": [1, 2],
    }


def test_missing_brave_never_uses_chrome(monkeypatch, tmp_path):
    monkeypatch.setattr("browser.os.path.isfile", lambda path: False)
    monkeypatch.setattr("browser.shutil.which", lambda name: None)
    monkeypatch.delenv("IRA_BRAVE_EXECUTABLE", raising=False)
    with pytest.raises(RuntimeError, match="Brave"):
        _launch_options(
            {
                "browser": "brave",
                "user_data_dir": str(tmp_path),
                "profile_directory": "Default",
            },
            True,
        )


def test_procedure_lookup_never_escapes_root_or_bypasses_platform(tmp_path):
    root = tmp_path / "skills"
    catalog = SkillCatalog(root, platform_override="windows")
    catalog.create_skill(
        "linux-only",
        "---\nname: linux-only\nplatforms: [linux]\n---\nOnly Linux",
        category="system",
    )
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "SKILL.md").write_text("Secret procedure", encoding="utf-8")
    assert catalog.get_skill("system/linux-only") is None
    assert catalog.get_skill("../secret") is None


@pytest.mark.parametrize("method,arg", [("navigate", "https://example.test")])
def test_revoked_action_does_not_close_or_launch_browsers(method, arg):
    page = FakePage()
    context = FakeContext([page])
    browser = controller_with(context=context)

    def guard():
        raise RuntimeError("stopped")

    browser.input_guard = guard
    with pytest.raises(RuntimeError, match="stopped"):
        getattr(browser, method)(arg)
    assert not context.closed and not page.visited
    browser.close()
