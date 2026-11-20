"""Real Brave checks for eligibility changes in the final prepared-click read."""

import os
from urllib.parse import quote

import pytest

from browser import BrowserController

pytestmark = pytest.mark.skipif(
    os.environ.get("IRA_RUN_BROWSER_EVALS") != "1",
    reason="Set IRA_RUN_BROWSER_EVALS=1 to launch isolated headless Brave",
)


@pytest.fixture(scope="module")
def controller(tmp_path_factory):
    directory = tmp_path_factory.mktemp("prepared-click-profile") / "Default"
    previous = os.environ.get("IRA_BROWSER_PROFILE")
    os.environ["IRA_BROWSER_PROFILE"] = str(directory)
    browser = BrowserController(headless=True, browser="brave")
    try:
        browser.navigate("about:blank")
        yield browser
    finally:
        browser.close()
        if previous is None:
            os.environ.pop("IRA_BROWSER_PROFILE", None)
        else:
            os.environ["IRA_BROWSER_PROFILE"] = previous


@pytest.mark.parametrize(
    "mutation",
    [
        "node.disabled = true",
        "node.closest('fieldset').disabled = true",
        "node.setAttribute('aria-disabled', 'true')",
        "node.parentElement.setAttribute('aria-disabled', 'true')",
    ],
)
def test_becoming_disabled_during_final_proof_dispatches_no_click(
    controller, monkeypatch, mutation
):
    from playwright.sync_api import ElementHandle

    controller.navigate(
        "data:text/html,"
        + quote(
            '<fieldset><div><button aria-label="Continue" '
            'onclick="window.clicked=true">Continue</button></div></fieldset>'
        )
    )
    controller.list_elements()
    evaluate = ElementHandle.evaluate
    proof_reads = []
    mouse_calls = []

    def disable_at_last_read(element, expression, arg=None, **kwargs):
        if "elementFromPoint" in expression:
            proof_reads.append(expression)
            if len(proof_reads) == 2:
                evaluate(element, "node => {" + mutation + ";}")
        return evaluate(element, expression, arg, **kwargs)

    monkeypatch.setattr(ElementHandle, "evaluate", disable_at_last_read)
    monkeypatch.setattr(
        controller._page.mouse,
        "click",
        lambda *args, **kwargs: mouse_calls.append((args, kwargs)),
    )
    with pytest.raises(RuntimeError, match=r"changed|covered"):
        controller.click(1)
    assert len(proof_reads) == 2
    assert mouse_calls == []
    assert controller.evaluate("() => window.clicked || false") is False
