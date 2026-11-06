"""Opt-in Selenium smoke using a disposable browser and loopback-only page.

Run with IRA_RUN_BROWSER_SMOKE=1. No personal browser profile or provider keys
are used. Normal unit runs skip browser startup and driver resolution.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from browser import BrowserController
from skill_adapters import SkillToolAdapters
from skill_execution import SkillExecutionService
from skill_format import Action, Check, Skill, SkillInput, Step
from skill_runs import SkillRunStore
from telemetry import SessionStatusReporter

pytestmark = pytest.mark.skipif(
    os.getenv("IRA_RUN_BROWSER_SMOKE") != "1", reason="opt-in real Selenium fixture"
)

HTML = b"""<!doctype html><title>IRA browser fixture</title>
<h1>IRA browser fixture</h1><label>Name <input id="name"></label>
<button id="finish" onclick="document.querySelector('#result').textContent='Completed';
this.dataset.count=String(Number(this.dataset.count||0)+1)">Finish</button>
<p id="result"></p><input type="password" id="password">
<button class="ambiguous">First</button><button class="ambiguous">Second</button>"""


@pytest.fixture
def real_browser():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = BrowserController()
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1100,800")
    try:
        browser._driver = webdriver.Chrome(options=options)
        browser._driver.set_page_load_timeout(15)
        yield browser, f"http://127.0.0.1:{server.server_port}/"
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


async def test_real_browser_skill_waits_types_verifies_and_never_replays(
    tmp_path, real_browser
):
    browser, url = real_browser
    adapters = SkillToolAdapters(browser, None, None)
    registry = adapters.registry()
    service = SkillExecutionService(
        verifier=adapters.verify,
        check_validator=adapters.validate_check,
        run_store=SkillRunStore(tmp_path / "runs.sqlite3"),
        supported_checks=("page_element", "page_value", "page_contains"),
    )
    skill = Skill(
        name="Local form smoke",
        description="Fill the local test form.",
        state="enabled",
        allowed_tools=("browse_to", "browser_type", "browser_click"),
        inputs=(SkillInput("name", "The displayed name"),),
        steps=(
            Step(
                1,
                "Open fixture",
                action=Action("tool", tool="browse_to", args=(("url", url),)),
                check=Check("page_element", "#name"),
            ),
            Step(2, "Ask name", action=Action("ask", param="name", text="Which name?")),
            Step(
                3,
                "Fill name",
                action=Action(
                    "tool",
                    tool="browser_type",
                    args=(
                        ("selector", "#name"),
                        ("text", "{name}"),
                        ("expected_url", url),
                    ),
                ),
                check=Check(
                    "page_value", json.dumps({"selector": "#name", "value": "{name}"})
                ),
            ),
            Step(
                4,
                "Finish",
                action=Action(
                    "tool",
                    tool="browser_click",
                    args=(("selector", "#finish"), ("expected_url", url)),
                ),
                check=Check("page_contains", "Completed"),
            ),
        ),
        done_check=Check("page_contains", "Completed"),
    )
    try:
        waiting = await service.run(
            skill,
            registry,
            SessionStatusReporter(sink=lambda _: None),
            run_id="live-browser",
        )
        assert waiting.status == "waiting"
        assert waiting.completed_steps == 1
        result = await service.answer(
            waiting.run_id, 'Ada "Lovelace" \\ example', question_id=waiting.question_id
        )
        assert result.status == "done", result.message
        assert result.completed_steps == 4
        assert (
            browser._driver.find_element("id", "name").get_attribute("value")
            == 'Ada "Lovelace" \\ example'
        )
        repeated = await service.run(
            skill,
            registry,
            SessionStatusReporter(sink=lambda _: None),
            run_id="live-browser",
        )
        assert repeated.status == "done"
        assert (
            browser._driver.find_element("id", "finish").get_attribute("data-count")
            == "1"
        )
        assert "Lovelace" not in json.dumps(service.status("live-browser"))
    finally:
        await service.aclose()


def test_real_browser_rejects_ambiguity_password_fields_and_url_drift(real_browser):
    browser, url = real_browser
    browser.navigate(url)
    with pytest.raises(ValueError, match="exactly one"):
        browser.click_selector(".ambiguous", url)
    with pytest.raises(ValueError, match="password"):
        browser.type_selector("#password", "fixture-only", url)
    with pytest.raises(ValueError, match="expected URL"):
        browser.type_selector("#name", "wrong scope", url + "different")
    assert browser._driver.find_element("id", "password").get_attribute("value") == ""
    assert browser._driver.find_element("id", "name").get_attribute("value") == ""
