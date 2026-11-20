"""Playwright ownership and request context must survive host thread changes."""

import contextvars
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from browser import BrowserController, _launch_options


def test_browser_has_marshalled_native_inspection_without_a_driver_api():
    browser = BrowserController(headless=True)
    assert callable(getattr(browser, "evaluate", None))
    assert callable(getattr(browser, "page_html", None))
    assert not hasattr(browser, "_driver")


def test_launch_keeps_audio_and_widevine_component_registration(monkeypatch, tmp_path):
    monkeypatch.delenv("IRA_CHROME_EXECUTABLE", raising=False)
    options = _launch_options(
        {
            "browser": "chrome",
            "user_data_dir": str(tmp_path),
            "profile_directory": "Default",
        },
        False,
    )
    assert options["ignore_default_args"] == [
        "--mute-audio",
        "--disable-component-update",
    ]
    assert options["channel"] == "chrome"
    assert options["timeout"] == 20_000


def worker_class():
    assert importlib.util.find_spec("browser_runtime") is not None
    from browser_runtime import BrowserWorker

    return BrowserWorker


def test_worker_uses_one_owner_thread_for_different_callers():
    worker = worker_class()()
    try:
        with ThreadPoolExecutor(max_workers=3) as callers:
            results = list(
                callers.map(lambda _: worker.run(threading.get_ident), range(8))
            )
        assert len(set(results)) == 1
        assert results[0] != threading.get_ident()
    finally:
        worker.close()


def test_worker_copies_context_and_nested_calls_keep_the_same_owner():
    marker = contextvars.ContextVar("test_browser_request", default="absent")
    worker = worker_class()()
    token = marker.set("current-request")
    try:
        result = worker.run(lambda: (marker.get(), worker.run(threading.get_ident)))
        assert result[0] == "current-request"
        assert result[1] == worker.run(threading.get_ident)
    finally:
        marker.reset(token)
        worker.close()


def test_worker_close_releases_the_thread_and_can_be_reopened():
    worker = worker_class()()
    worker.run(lambda: None)
    executor = worker._executor
    worker.close()
    assert all(not thread.is_alive() for thread in executor._threads)
    try:
        assert worker.run(lambda: "reopened") == "reopened"
    finally:
        worker.close()


def test_revoked_launch_keeps_stop_error_and_releases_runtime(monkeypatch):
    effects = []
    monkeypatch.setattr("browser._launch_options", lambda *args: {})
    runtime = SimpleNamespace(
        chromium=SimpleNamespace(
            launch_persistent_context=lambda *a, **k: effects.append("launched")
        ),
        stop=lambda: effects.append("runtime stopped"),
    )
    browser = BrowserController(playwright_factory=lambda: runtime)
    calls = 0

    class RequestStoppedError(RuntimeError):
        pass

    def guard():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RequestStoppedError("request revoked")

    browser.input_guard = guard
    try:
        with pytest.raises(RequestStoppedError, match="revoked"):
            browser.navigate("https://example.test")
        assert effects == ["runtime stopped"]
        assert not browser.is_open
    finally:
        browser.close()


def test_context_and_runtime_cleanup_stay_on_native_owner():
    from tests.fixtures.playwright_fakes import FakeContext, controller_with

    effects = []
    context = FakeContext()
    original_close = context.close

    def close_context():
        effects.append(("context", threading.get_ident()))
        original_close()

    context.close = close_context
    browser = controller_with(context=context)
    owner = browser._worker.run(threading.get_ident)
    browser._worker.run(
        lambda: setattr(
            browser,
            "_playwright",
            SimpleNamespace(
                stop=lambda: effects.append(("runtime", threading.get_ident()))
            ),
        )
    )
    browser.close()
    assert effects == [("context", owner), ("runtime", owner)]
    assert not browser.is_open
