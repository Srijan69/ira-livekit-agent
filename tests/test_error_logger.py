"""Unit tests for error logging and desktop fallback integration."""

import pytest
from livekit.agents import ToolError

from agent import Assistant
from error_logger import DEFAULT_ERROR_LOG_DIR, get_error_log_dir, log_tool_error


def test_get_error_log_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("IRA_ERROR_LOG_DIR", raising=False)
    # Default directory
    d = get_error_log_dir()
    assert d == DEFAULT_ERROR_LOG_DIR
    assert d.exists()


def test_get_error_log_dir_custom(monkeypatch, tmp_path):
    custom = tmp_path / "custom_errors"
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(custom))
    d = get_error_log_dir()
    assert d == custom
    assert custom.exists()


def test_log_tool_error_creates_structured_log(monkeypatch, tmp_path):
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(tmp_path))

    exc = RuntimeError("Element click intercepted by overlay")
    log_file = log_tool_error(
        tool_name="click_element",
        error=exc,
        args={"index": 3},
        task_context="Play phonk playlist on Spotify",
        recovery_advice="Call capture_screen to inspect visually and use click_desktop_element.",
    )

    assert log_file.exists()
    assert log_file.parent == tmp_path
    content = log_file.read_text(encoding="utf-8")

    assert "IRA TOOL EXECUTION ERROR LOG" in content
    assert "Tool:         click_element" in content
    assert "Task Context: Play phonk playlist on Spotify" in content
    assert "Error Type:   RuntimeError" in content
    assert "Element click intercepted by overlay" in content
    assert '"index": 3' in content
    assert "Recommended Fallback:" in content
    assert "Call capture_screen" in content


@pytest.mark.asyncio
async def test_browser_call_error_logs_and_returns_fallback_advice(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(tmp_path))

    class BrokenBrowser:
        def navigate(self, url: str):
            raise RuntimeError("net::ERR_CONNECTION_REFUSED")

    assistant = Assistant(browser=BrokenBrowser())

    with pytest.raises(ToolError) as exc_info:
        await assistant.browse_to(None, url="https://broken.example.com")

    err_msg = str(exc_info.value)
    assert "Could not open that page" in err_msg
    assert "Error logged to" in err_msg
    assert "Fallback: Call capture_screen now" in err_msg

    # Verify log file was written to tmp_path
    logs = list(tmp_path.glob("error_*_open_that_page_*.log"))
    assert len(logs) == 1
    log_content = logs[0].read_text(encoding="utf-8")
    assert "net::ERR_CONNECTION_REFUSED" in log_content
    assert "BrokenBrowser" in log_content or "navigate" in log_content
