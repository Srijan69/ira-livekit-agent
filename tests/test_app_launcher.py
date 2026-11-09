"""Installed-app selection; the Windows catalogue and launch boundary are fakes."""

from types import SimpleNamespace

import pytest

import app_launcher
from app_launcher import InstalledApp, launch_installed_app, select_installed_app

APPS = [
    InstalledApp("Notepad", "Microsoft.WindowsNotepad_test!App"),
    InstalledApp("Calculator", "Microsoft.WindowsCalculator_test!App"),
    InstalledApp("Google Chrome", "Chrome"),
]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("  NOTEPAD ", "Microsoft.WindowsNotepad_test!App"),
        ("calc", "Microsoft.WindowsCalculator_test!App"),
        ("Chrome", "Chrome"),
    ],
)
def test_selects_only_exact_registered_name_or_fixed_alias(name, expected):
    assert select_installed_app(name, APPS).app_id == expected


@pytest.mark.parametrize("name", ["ZzQqNotInstalledApp", "Note", "", "Chrome; cmd"])
def test_missing_name_never_selects_a_search_or_substring(name):
    with pytest.raises(ValueError):
        select_installed_app(name, APPS)


def test_duplicate_catalogue_rows_do_not_make_same_target_ambiguous():
    assert select_installed_app("Notepad", [APPS[0], APPS[0]]) == APPS[0]


def test_distinct_apps_with_same_name_require_clarification():
    with pytest.raises(ValueError, match="ambiguous"):
        select_installed_app(
            "Notepad", [APPS[0], InstalledApp("Notepad", "Other.Notepad!App")]
        )


@pytest.fixture
def native_boundaries(monkeypatch):
    started = []
    monkeypatch.setattr(app_launcher, "_read_start_apps", lambda: APPS)
    monkeypatch.setattr(app_launcher, "_start_app", started.append)
    return started


def test_unknown_app_has_no_launch_side_effect(native_boundaries):
    with pytest.raises(ValueError, match="not installed"):
        launch_installed_app("ZzQqNotInstalledApp")
    assert native_boundaries == []


def test_launch_uses_only_the_selected_registered_target(native_boundaries):
    selected = launch_installed_app("Notepad")
    assert selected == APPS[0]
    assert native_boundaries == [APPS[0]]


def test_reads_catalogue_with_fixed_hidden_bounded_command(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            stdout='[{"Name":"Notepad","AppID":"Microsoft.WindowsNotepad_test!App"}]'
        )

    monkeypatch.setattr(app_launcher, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        app_launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    monkeypatch.setattr(app_launcher.subprocess, "run", run)
    assert app_launcher._read_start_apps() == [APPS[0]]
    args, options = calls[0]
    assert "Get-StartApps" in args[-1]
    assert "Notepad" not in args[-1]
    assert options["creationflags"] == app_launcher.subprocess.CREATE_NO_WINDOW
    assert options["timeout"] == 8
    assert options["check"] is True


def test_invalid_catalogue_is_not_treated_as_missing_app(monkeypatch):
    monkeypatch.setattr(app_launcher, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        app_launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    monkeypatch.setattr(
        app_launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="not JSON"),
    )
    with pytest.raises(RuntimeError, match="catalogue"):
        app_launcher._read_start_apps()
