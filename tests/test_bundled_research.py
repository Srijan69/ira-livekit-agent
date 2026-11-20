"""The shipped research procedure routes work without unrelated effects."""

from pathlib import Path
from types import SimpleNamespace

from skills import SkillCatalog
from task_routing import decide_task


def test_deep_microsoft_request_selects_reviewed_research_procedure(tmp_path):
    import shutil

    root = Path(__file__).resolve().parents[1] / "skills"
    shutil.copytree(
        root, tmp_path / "skills", ignore=shutil.ignore_patterns("*.sqlite3*")
    )
    catalog = SkillCatalog(tmp_path / "skills", platform_override="windows")
    request = SimpleNamespace(
        id="research-request", text="Do a deep search about Microsoft", answers=()
    )
    result = decide_task(
        request,
        catalog,
        available_tools=(
            "browse_to",
            "read_page",
            "list_page_elements",
            "click_element",
            "type_into_element",
            "research_status",
        ),
    )
    assert result.mode == "procedure"
    assert result.selected_id == "procedure:web/research-topic"
    assert "open_app" not in result.allowed_tools
