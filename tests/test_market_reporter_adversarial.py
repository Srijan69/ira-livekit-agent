"""Empirical adversarial stress tests for Milestone 2: Market Reporting & Excel Generation.

Adversarial test suite covering:
1. All negative percentage changes (e.g. [-5.0, -10.0, -2.0] -> -2.0 is top performer and highlighted).
2. Tied highest percentage changes (positive, negative, identical).
3. Empty asset list and malformed rows (missing fields, corrupted values, malformed HTML).
4. PermissionError / locked file when Market_Report.xlsx is open (real OS file lock & diagnostic logging).
5. Live scraper offline fallback behavior (default mock fixture, unreachable URL error logging, browser fallbacks).
6. Adversarial asset name boundary cases in executive summary generation.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import openpyxl
import pytest
from livekit.agents import ToolError

from agent import Assistant
from market_report import (
    compose_market_summary,
    create_market_report,
    find_top_performer,
    scrape_market_assets,
    validate_three_sentences,
)


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


# ===========================================================================
# 1. All Negative Percentage Changes
# ===========================================================================


def test_all_negative_percentages_find_top_performer():
    """Verify that among all negative changes, the least negative (max) is selected."""
    data = [
        {"name": "Bearish A", "price": "$100.00", "change_24h": -5.0},
        {"name": "Bearish B", "price": "$50.00", "change_24h": -10.0},
        {
            "name": "Bearish C",
            "price": "$75.00",
            "change_24h": -2.0,
        },  # Least negative -> top performer
    ]
    top = find_top_performer(data)
    assert top["name"] == "Bearish C"
    assert top["change_24h"] == pytest.approx(-2.0)


def test_all_negative_percentages_excel_green_highlight(tmp_path):
    """Verify Excel generation with all negative percentages highlights the least negative row in green."""
    data = [
        {"name": "Asset Neg1", "price": "$120.00", "change_24h": -5.0},
        {"name": "Asset Neg2", "price": "$85.50", "change_24h": -10.0},
        {
            "name": "Asset Neg3",
            "price": "$42.10",
            "change_24h": -2.0,
        },  # Top performer: index 2 -> row 4
        {"name": "Asset Neg4", "price": "$310.00", "change_24h": -15.5},
        {"name": "Asset Neg5", "price": "$15.00", "change_24h": -8.4},
    ]
    output_path = tmp_path / "Market_Report_Neg.xlsx"
    created = create_market_report(data, output_path=output_path)
    assert created.exists()

    wb = openpyxl.load_workbook(created)
    ws = wb.active

    # Row 4 (Asset Neg3: -2.0%) MUST have green fill
    for col_idx in range(1, 4):
        cell = ws.cell(row=4, column=col_idx)
        assert cell.fill.fill_type == "solid"
        color = str(
            getattr(cell.fill.start_color, "rgb", "")
            or getattr(cell.fill.start_color, "value", "")
        )
        assert any(g in color.upper() for g in ("90EE90", "C6EFCE"))

    # Rows 2, 3, 5, 6 MUST NOT have green fill
    for non_winning_row in (2, 3, 5, 6):
        cell = ws.cell(row=non_winning_row, column=3)
        color = str(
            getattr(cell.fill.start_color, "rgb", "")
            or getattr(cell.fill.start_color, "value", "")
        )
        assert not any(g in color.upper() for g in ("90EE90", "C6EFCE"))

    # Verify Column C string format for negative change
    assert ws.cell(row=4, column=3).value == "-2.00%"
    assert ws.cell(row=2, column=3).value == "-5.00%"
    wb.close()


def test_all_negative_percentages_near_zero_spread(tmp_path):
    """Verify precision handling when all percentages are negative and close to zero."""
    data = [
        {"name": "A", "price": "$10", "change_24h": -0.50},
        {"name": "B", "price": "$10", "change_24h": -0.01},  # Top performer -> row 3
        {"name": "C", "price": "$10", "change_24h": -0.05},
    ]
    out = create_market_report(data, output_path=tmp_path / "Near_Zero.xlsx")
    wb = openpyxl.load_workbook(out)
    ws = wb.active

    cell = ws.cell(row=3, column=3)
    color = str(
        getattr(cell.fill.start_color, "rgb", "")
        or getattr(cell.fill.start_color, "value", "")
    )
    assert any(g in color.upper() for g in ("90EE90", "C6EFCE"))
    assert cell.value == "-0.01%"
    wb.close()


def test_all_negative_percentages_summary_composition():
    """Verify executive summary generation succeeds with negative 24h change."""
    top_negative = {"name": "Defensive Stock", "price": "$45.00", "change_24h": -1.5}
    summary = compose_market_summary(top_negative)
    assert validate_three_sentences(summary) is True
    assert "-1.5" in summary
    assert "Defensive Stock" in summary


# ===========================================================================
# 2. Tied Highest Percentage Change
# ===========================================================================


def test_tied_highest_percentage_change_positive(tmp_path):
    """Verify handling of tied highest positive percentage change.

    Deterministic behavior: first occurrence is selected as top performer,
    highlighted in green, without error.
    """
    tied_data = [
        {
            "name": "Leader Alpha",
            "price": "$100.00",
            "change_24h": 10.0,
        },  # Tied max 1 -> row 2
        {
            "name": "Leader Beta",
            "price": "$200.00",
            "change_24h": 10.0,
        },  # Tied max 2 -> row 3
        {"name": "Follower Gamma", "price": "$50.00", "change_24h": 2.5},
    ]
    top = find_top_performer(tied_data)
    assert top["name"] == "Leader Alpha"

    out = create_market_report(tied_data, output_path=tmp_path / "Tied_Pos.xlsx")
    wb = openpyxl.load_workbook(out)
    ws = wb.active

    # Row 2 (Leader Alpha) is highlighted green
    for col_idx in range(1, 4):
        cell = ws.cell(row=2, column=col_idx)
        assert cell.fill.fill_type == "solid"
        color = str(
            getattr(cell.fill.start_color, "rgb", "")
            or getattr(cell.fill.start_color, "value", "")
        )
        assert any(g in color.upper() for g in ("90EE90", "C6EFCE"))

    # Row 3 (Leader Beta) is not highlighted
    cell_beta = ws.cell(row=3, column=3)
    color_beta = str(
        getattr(cell_beta.fill.start_color, "rgb", "")
        or getattr(cell_beta.fill.start_color, "value", "")
    )
    assert not any(g in color_beta.upper() for g in ("90EE90", "C6EFCE"))
    wb.close()


def test_tied_highest_percentage_all_identical(tmp_path):
    """Verify spreadsheet handles edge case where all assets have identical percentage changes."""
    identical_data = [
        {"name": f"Asset {i}", "price": "$10.00", "change_24h": 0.0} for i in range(5)
    ]
    out = create_market_report(identical_data, output_path=tmp_path / "Identical.xlsx")
    wb = openpyxl.load_workbook(out)
    ws = wb.active

    # Row 2 (first asset) is highlighted green, subsequent rows are not
    cell_first = ws.cell(row=2, column=3)
    color_first = str(
        getattr(cell_first.fill.start_color, "rgb", "")
        or getattr(cell_first.fill.start_color, "value", "")
    )
    assert any(g in color_first.upper() for g in ("90EE90", "C6EFCE"))

    cell_second = ws.cell(row=3, column=3)
    color_second = str(
        getattr(cell_second.fill.start_color, "rgb", "")
        or getattr(cell_second.fill.start_color, "value", "")
    )
    assert not any(g in color_second.upper() for g in ("90EE90", "C6EFCE"))
    wb.close()


def test_tied_highest_percentage_negative(tmp_path):
    """Verify tied highest among all negative percentages."""
    tied_neg = [
        {"name": "Tie Neg 1", "price": "$10.00", "change_24h": -3.5},
        {"name": "Tie Neg 2", "price": "$20.00", "change_24h": -3.5},
        {"name": "Tie Neg 3", "price": "$30.00", "change_24h": -8.0},
    ]
    top = find_top_performer(tied_neg)
    assert top["name"] == "Tie Neg 1"

    out = create_market_report(tied_neg, output_path=tmp_path / "Tied_Neg.xlsx")
    wb = openpyxl.load_workbook(out)
    ws = wb.active
    cell = ws.cell(row=2, column=3)
    color = str(
        getattr(cell.fill.start_color, "rgb", "")
        or getattr(cell.fill.start_color, "value", "")
    )
    assert any(g in color.upper() for g in ("90EE90", "C6EFCE"))
    wb.close()


# ===========================================================================
# 3. Empty Asset List and Malformed Rows
# ===========================================================================


def test_empty_asset_list_raises_explicit_value_errors(tmp_path):
    """Verify empty asset list raises clear ValueError in core functions."""
    with pytest.raises(ValueError, match="empty asset data"):
        create_market_report([], output_path=tmp_path / "Empty.xlsx")

    with pytest.raises(ValueError, match="cannot be empty"):
        find_top_performer([])

    with pytest.raises(ValueError, match="cannot be empty"):
        compose_market_summary({})


def test_malformed_rows_missing_fields_defaults_safely(tmp_path):
    """Verify rows missing price or change fields default safely without crashing."""
    malformed_data = [
        {"name": "Only Name"},
        {"name": "Only Price", "price": "$100.00"},
        {"name": "Valid Winner", "price": "$50.00", "change_24h": 5.0},
    ]
    out = create_market_report(
        malformed_data, output_path=tmp_path / "Missing_Fields.xlsx"
    )
    assert out.exists()

    wb = openpyxl.load_workbook(out)
    ws = wb.active

    # Row 2 (Only Name): Price defaults to $0.00, change to 0.00%
    assert ws.cell(row=2, column=1).value == "Only Name"
    assert ws.cell(row=2, column=2).value == "$0.00"
    assert ws.cell(row=2, column=3).value in ("0.00%", "+0.00%")

    # Row 4 (Valid Winner 5.0%) is highlighted green
    cell_winner = ws.cell(row=4, column=3)
    color = str(
        getattr(cell_winner.fill.start_color, "rgb", "")
        or getattr(cell_winner.fill.start_color, "value", "")
    )
    assert any(g in color.upper() for g in ("90EE90", "C6EFCE"))
    wb.close()


def test_malformed_rows_corrupt_change_values(tmp_path):
    """Verify rows with unparseable change values do not cause crash."""
    corrupt_data = [
        {"name": "Corrupt 1", "price": "$10.00", "change_24h": "N/A"},
        {"name": "Corrupt 2", "price": "$20.00", "change_24h": "UNKNOWN_VAL"},
        {"name": "Valid Asset", "price": "$30.00", "change_24h": 3.2},
    ]
    out = create_market_report(
        corrupt_data, output_path=tmp_path / "Corrupt_Change.xlsx"
    )
    wb = openpyxl.load_workbook(out)
    ws = wb.active

    # Row 4 (Valid Asset) receives green highlight
    cell = ws.cell(row=4, column=3)
    color = str(
        getattr(cell.fill.start_color, "rgb", "")
        or getattr(cell.fill.start_color, "value", "")
    )
    assert any(g in color.upper() for g in ("90EE90", "C6EFCE"))
    wb.close()


def test_malformed_rows_all_corrupt_change_no_highlight(tmp_path):
    """Verify that when all rows have unparseable change, report generates with no green highlight."""
    all_corrupt = [
        {"name": "Corrupt 1", "price": "$10.00", "change_24h": "INVALID"},
        {"name": "Corrupt 2", "price": "$20.00", "change_24h": "UNKNOWN"},
    ]
    out = create_market_report(all_corrupt, output_path=tmp_path / "All_Corrupt.xlsx")
    wb = openpyxl.load_workbook(out)
    ws = wb.active

    for row_idx in (2, 3):
        cell = ws.cell(row=row_idx, column=3)
        color = str(
            getattr(cell.fill.start_color, "rgb", "")
            or getattr(cell.fill.start_color, "value", "")
        )
        assert not any(g in color.upper() for g in ("90EE90", "C6EFCE"))
    wb.close()


def test_malformed_html_scraper_skips_bad_rows():
    """Verify HTML scraper skips malformed rows and extracts valid rows."""
    malformed_html = """
    <table>
        <tr><th>Name</th><th>Price</th><th>24h Change</th></tr>
        <tr><td>Corrupt Row 1</td><td>N/A</td><td>N/A</td></tr>
        <tr><td></td><td></td><td></td></tr>
        <tr><td>Valid Asset</td><td>$150.00</td><td>+5.5%</td></tr>
        <tr><td>Corrupt Row 2</td><td>not a price</td><td>not a percentage</td></tr>
        <tr><td>Second Valid</td><td>$75.20</td><td>-1.2%</td></tr>
    </table>
    """
    assets = scrape_market_assets(html_content=malformed_html)
    assert len(assets) == 2
    assert assets[0]["name"] == "Valid Asset"
    assert assets[0]["price"] == "$150.00"
    assert assets[0]["change_24h"] == pytest.approx(5.5)

    assert assets[1]["name"] == "Second Valid"
    assert assets[1]["price"] == "$75.20"
    assert assets[1]["change_24h"] == pytest.approx(-1.2)


def test_malformed_html_no_valid_rows_raises_value_error():
    """Verify HTML scraper raises ValueError when all rows are malformed."""
    no_valid_html = """
    <table>
        <tr><th>Name</th><th>Price</th><th>24h Change</th></tr>
        <tr><td>Bad Row</td><td>N/A</td><td>N/A</td></tr>
    </table>
    """
    with pytest.raises(
        ValueError, match="No asset rows found matching market data schema"
    ):
        scrape_market_assets(html_content=no_valid_html)


# ===========================================================================
# 4. PermissionError / Locked File (Market_Report.xlsx Already Open)
# ===========================================================================


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows exclusive file locking test"
)
def test_locked_file_real_os_lock_raises_permission_error_and_logs(
    tmp_path, monkeypatch
):
    """Empirically test real Windows OS file lock via msvcrt.locking and verify diagnostic logging."""
    import msvcrt

    log_dir = tmp_path / "errors"
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(log_dir))

    locked_file = tmp_path / "Market_Report_Locked.xlsx"
    locked_file.write_bytes(b"initial lock placeholder")

    # Acquire non-blocking exclusive lock
    fd = os.open(str(locked_file), os.O_RDWR)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 24)

    try:
        data = [{"name": "Asset A", "price": "$10.00", "change_24h": 1.0}]
        with pytest.raises(PermissionError) as exc_info:
            create_market_report(data, output_path=locked_file)

        assert "Permission denied" in str(exc_info.value)
        assert locked_file.name in str(exc_info.value)

        # Verify structured error log was generated
        logs = list(log_dir.glob("error_*create_market_report*.log"))
        assert len(logs) >= 1
        log_content = logs[0].read_text(encoding="utf-8")
        assert "create_market_report" in log_content
        assert "PermissionError" in log_content
        assert (
            "Ensure Market_Report.xlsx is closed in Excel before saving" in log_content
        )
    finally:
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 24)
        os.close(fd)


async def test_locked_file_assistant_generate_market_report_tool_error(
    tmp_path, monkeypatch
):
    """Verify Assistant.generate_market_report catches PermissionError, writes diagnostic, and raises ToolError."""
    log_dir = tmp_path / "errors"
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(log_dir))

    mock_assets = [
        {"name": "Asset A", "price": "$10.00", "change_24h": 1.0},
    ]

    def locked_create(data, output_path):
        raise PermissionError(
            f"Permission denied while saving '{Path(output_path).name}'. The file may be open in Excel."
        )

    monkeypatch.setattr("market_report.create_market_report", locked_create)

    assistant = Assistant()
    with (
        patch("market_report.scrape_market_assets", return_value=mock_assets),
        pytest.raises(ToolError) as exc_info,
    ):
        await assistant.generate_market_report(
            None,
            source_url="https://mock.test",
            output_path=str(tmp_path / "Locked_Report.xlsx"),
        )

    err_msg = str(exc_info.value)
    assert "Permission denied" in err_msg
    assert "Logged diagnostic to" in err_msg

    logs = list(log_dir.glob("error_*generate_market_report*.log"))
    assert len(logs) >= 1
    log_text = logs[0].read_text(encoding="utf-8")
    assert "generate_market_report" in log_text
    assert "PermissionError" in log_text
    assert "ensure Market_Report.xlsx is closed if open in Excel" in log_text


# ===========================================================================
# 5. Live Scraper Offline Fallback Behavior
# ===========================================================================


def test_live_scraper_offline_default_fallback():
    """Verify calling scrape_market_assets without URL or browser defaults to offline mock fixture."""
    assets = scrape_market_assets()
    assert len(assets) == 5
    names = [a["name"] for a in assets]
    assert any("NVIDIA" in n for n in names)
    assert any("Apple" in n for n in names)
    assert any("Tesla" in n for n in names)


def test_live_scraper_unreachable_network_url_logs_diagnostic_and_raises(
    tmp_path, monkeypatch
):
    """Verify live scraper against offline/unreachable network address writes structured diagnostic log."""
    log_dir = tmp_path / "errors"
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(log_dir))

    offline_url = "http://offline-unreachable-test-domain-12345.test/trending"
    with pytest.raises(URLError) as exc_info:
        scrape_market_assets(source_url=offline_url, timeout=1.0)

    assert (
        "getaddrinfo failed" in str(exc_info.value)
        or "URLError" in exc_info.type.__name__
    )

    # Verify diagnostic log was recorded
    logs = list(log_dir.glob("error_*scrape_trending_assets*.log"))
    assert len(logs) >= 1
    log_content = logs[0].read_text(encoding="utf-8")
    assert "scrape_trending_assets" in log_content
    assert "URLError" in log_content
    assert offline_url in log_content
    assert (
        "Verify table HTML hydration or provide tests/fixtures/market_mock.html."
        in log_content
    )


def test_live_scraper_browser_page_source_fallback():
    """Verify that when browser JS extraction fails, scraper falls back to page_source."""
    mock_driver = MagicMock()
    # JS execution raises exception (e.g. script error or restricted context)
    mock_driver.execute_script.side_effect = RuntimeError("JS execution disabled")
    mock_driver.page_source = """
    <table>
        <tr><th>Name</th><th>Price</th><th>24h Change</th></tr>
        <tr><td>Fallback Coin</td><td>$42.00</td><td>+7.8%</td></tr>
    </table>
    """

    mock_browser = MagicMock()
    mock_browser._driver = mock_driver
    mock_browser.is_open = True

    assets = scrape_market_assets(browser=mock_browser)
    assert len(assets) == 1
    assert assets[0]["name"] == "Fallback Coin"
    assert assets[0]["price"] == "$42.00"
    assert assets[0]["change_24h"] == pytest.approx(7.8)


def test_live_scraper_browser_read_page_text_fallback():
    """Verify that when both JS and page_source are unavailable, scraper falls back to read_page."""
    mock_browser = MagicMock(spec=["read_page", "is_open"])
    mock_browser.is_open = True
    mock_browser.read_page.return_value = (
        "Trending Assets:\nBitcoin $64,250.00 +4.15%\nEthereum $3,450.00 -1.20%\n"
    )

    assets = scrape_market_assets(browser=mock_browser)
    assert len(assets) == 2
    assert assets[0]["name"] == "Bitcoin"
    assert assets[0]["price"] == "$64,250.00"
    assert assets[0]["change_24h"] == pytest.approx(4.15)
    assert assets[1]["name"] == "Ethereum"
    assert assets[1]["change_24h"] == pytest.approx(-1.20)


# ===========================================================================
# 6. Executive Summary Boundary Cases (Adversarial Company Names)
# ===========================================================================


def test_summary_standard_top_performer_passes():
    """Verify standard asset with clean company name generates valid 3 sentences."""
    asset = {"name": "Tesla Inc.", "price": "$218.80", "change_24h": 12.3}
    summary = compose_market_summary(asset)
    assert validate_three_sentences(summary) is True


def test_summary_adversarial_name_with_exclamation_raises():
    """Expose boundary failure: asset name with exclamation mark (e.g. 'Yahoo!') breaks sentence splitter."""
    asset = {"name": "Yahoo! Finance Inc.", "price": "$100.00", "change_24h": 5.0}
    # Currently fails because Yahoo! splits the first sentence into two
    with pytest.raises(ValueError, match="exactly 3 sentences"):
        compose_market_summary(asset)


def test_summary_adversarial_name_with_single_letter_initialism_raises():
    """Expose boundary failure: single-letter initialisms (e.g. 'U.S. Bancorp') break sentence splitter."""
    asset = {"name": "U.S. Bancorp", "price": "$45.00", "change_24h": 2.5}
    # Currently fails because regex re.sub(r"\b([A-Za-z]{2,5})\.", ...) does not match single letters like 'U.' or 'S.'
    with pytest.raises(ValueError, match="exactly 3 sentences"):
        compose_market_summary(asset)
