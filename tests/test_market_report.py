"""Unit and integration test suite for Milestone 2: Financial Market Report & Excel Styling.

Covers:
- Extraction of trending assets from mock HTML table fixture
- Generation of Market_Report.xlsx workbook
- Exact column names validation: Name, Current Price, 24-hour Percentage Change
- Green fill (PatternFill) on row/cell with maximum 24h percentage change
- Executive summary composition with exactly 3 complete sentences
- Error handling on empty asset data
- Error handling on locked/permission-denied file with error_logger integration
- Assistant @function_tool wiring, CAPABILITIES registration, and request scope governance
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from livekit.agents import ToolError

from agent import Assistant
from market_report import (
    compose_market_summary,
    create_market_report,
    launch_summary_editor,
    scrape_market_assets,
    validate_three_sentences,
)
from request_scope import RequestScope, ScopeViolation
from skill_tools import CAPABILITIES

FIXTURE_HTML = Path("tests/fixtures/market_mock.html")

MOCK_ASSETS = [
    {"name": "NVIDIA Corporation", "price": "$135.50", "change_24h": 8.45},
    {"name": "Apple Inc.", "price": "$232.10", "change_24h": 1.25},
    {"name": "Tesla Inc.", "price": "$218.80", "change_24h": 12.30},  # MAX
    {"name": "Microsoft Corporation", "price": "$425.00", "change_24h": -0.65},
    {"name": "Amazon.com Inc.", "price": "$188.90", "change_24h": 3.10},
]


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# 1. HTML Scraping & Data Extraction
# ---------------------------------------------------------------------------


def test_extract_assets_from_mock_html_table():
    """Verify extraction of top 5 trending assets from HTML table fixture."""
    html = FIXTURE_HTML.read_text(encoding="utf-8")
    assets = scrape_market_assets(html_content=html)

    assert len(assets) == 5
    assert "NVIDIA" in assets[0]["name"]
    assert assets[0]["price"] == "$135.50"
    assert assets[0]["change_24h"] == pytest.approx(8.45)

    # Max performer (Tesla)
    top_performer = max(assets, key=lambda a: a["change_24h"])
    assert "Tesla" in top_performer["name"]
    assert top_performer["change_24h"] == pytest.approx(12.30)

    # Negative change (Microsoft)
    assert assets[3]["name"] == "Microsoft Corporation"
    assert assets[3]["change_24h"] == pytest.approx(-0.65)


def test_extract_assets_handles_price_and_percentage_variations():
    """Verify robust parsing of numbers with symbols, spaces, and signs."""
    custom_html = """
    <table>
        <tr><th>Name</th><th>Price</th><th>24h %</th></tr>
        <tr><td>Asset A</td><td>$1,234.56</td><td>+ 12.5%</td></tr>
        <tr><td>Asset B</td><td>$0.0052</td><td>-3.14%</td></tr>
        <tr><td>Asset C</td><td>100.00</td><td>0.0%</td></tr>
    </table>
    """
    assets = scrape_market_assets(html_content=custom_html)
    assert len(assets) == 3
    assert assets[0]["change_24h"] == 12.5
    assert assets[1]["change_24h"] == -3.14
    assert assets[2]["change_24h"] == 0.0


def test_extract_assets_empty_table_raises_error():
    """Verify ValueError is raised when table is empty or missing."""
    empty_html = "<html><body><p>No table here</p></body></html>"
    with pytest.raises(ValueError, match="No asset rows found"):
        scrape_market_assets(html_content=empty_html)


# ---------------------------------------------------------------------------
# 2. Spreadsheet Generation & Exact Column Specification
# ---------------------------------------------------------------------------


def test_create_market_report_generates_valid_xlsx(tmp_path):
    """Verify creation of a valid Excel file with 1 header row + 5 data rows."""
    output_path = tmp_path / "Market_Report.xlsx"
    res = create_market_report(MOCK_ASSETS, output_path=output_path)

    assert res.exists()
    wb = openpyxl.load_workbook(output_path)
    ws = wb.active
    assert ws.title == "Market Report"
    assert ws.max_row == 6  # 1 header + 5 assets
    assert ws.max_column == 3


def test_spreadsheet_column_names_match_exact_specification(tmp_path):
    """Verify Column A is Name, Column B is Current Price, Column C is 24-hour Percentage Change."""
    output_path = tmp_path / "Market_Report.xlsx"
    create_market_report(MOCK_ASSETS, output_path=output_path)

    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    assert ws.cell(row=1, column=1).value == "Name"
    assert ws.cell(row=1, column=2).value == "Current Price"
    assert ws.cell(row=1, column=3).value == "24-hour Percentage Change"


def test_spreadsheet_data_rows_correctly_populated(tmp_path):
    """Verify data rows match the asset list values."""
    output_path = tmp_path / "Market_Report.xlsx"
    create_market_report(MOCK_ASSETS, output_path=output_path)

    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    # Row 2 (NVIDIA)
    assert ws.cell(row=2, column=1).value == "NVIDIA Corporation"
    assert ws.cell(row=2, column=2).value == "$135.50"
    assert ws.cell(row=2, column=3).value == "+8.45%"

    # Row 4 (Tesla - max)
    assert ws.cell(row=4, column=1).value == "Tesla Inc."
    assert ws.cell(row=4, column=2).value == "$218.80"
    assert ws.cell(row=4, column=3).value == "+12.30%"


# ---------------------------------------------------------------------------
# 3. Green Styling (PatternFill) on Top Performer
# ---------------------------------------------------------------------------


def test_highest_percentage_change_row_has_green_fill(tmp_path):
    """Verify the row/cell with max(change_24h) has solid green PatternFill."""
    output_path = tmp_path / "Market_Report.xlsx"
    create_market_report(MOCK_ASSETS, output_path=output_path)

    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    # Tesla is at index 2 -> row 4 (1 header + 3rd asset = row 4)
    target_row = 4
    for col in range(1, 4):
        cell = ws.cell(row=target_row, column=col)
        fill = cell.fill
        assert fill.fill_type == "solid"
        color_val = str(
            getattr(fill.start_color, "rgb", "")
            or getattr(fill.start_color, "value", "")
        )
        assert any(g in color_val.upper() for g in ("90EE90", "C6EFCE"))


def test_non_highest_rows_do_not_have_green_fill(tmp_path):
    """Verify that rows without max percentage change do NOT receive green highlight."""
    output_path = tmp_path / "Market_Report.xlsx"
    create_market_report(MOCK_ASSETS, output_path=output_path)

    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    # Row 2 (NVIDIA +8.45%)
    nvda_fill = ws.cell(row=2, column=3).fill
    nvda_color = str(
        getattr(nvda_fill.start_color, "rgb", "")
        or getattr(nvda_fill.start_color, "value", "")
    )
    assert not any(g in nvda_color.upper() for g in ("90EE90", "C6EFCE"))

    # Row 5 (Microsoft -0.65%)
    msft_fill = ws.cell(row=5, column=3).fill
    msft_color = str(
        getattr(msft_fill.start_color, "rgb", "")
        or getattr(msft_fill.start_color, "value", "")
    )
    assert not any(g in msft_color.upper() for g in ("90EE90", "C6EFCE"))


def test_all_negative_percentages_highlights_correct_max(tmp_path):
    """Verify max selection when all percentage changes are negative."""
    negative_assets = [
        {"name": "Asset 1", "price": "$10", "change_24h": -10.5},
        {"name": "Asset 2", "price": "$20", "change_24h": -1.2},  # MAX (-1.2 > -10.5)
        {"name": "Asset 3", "price": "$30", "change_24h": -5.0},
    ]
    output_path = tmp_path / "Market_Report.xlsx"
    create_market_report(negative_assets, output_path=output_path)

    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    # Asset 2 is row 3
    target_fill = ws.cell(row=3, column=3).fill
    color_val = str(
        getattr(target_fill.start_color, "rgb", "")
        or getattr(target_fill.start_color, "value", "")
    )
    assert any(g in color_val.upper() for g in ("90EE90", "C6EFCE"))


# ---------------------------------------------------------------------------
# 4. Executive Summary Composition (Exact 3 Sentences)
# ---------------------------------------------------------------------------


def test_compose_market_summary_has_exactly_three_sentences():
    """Verify summary of top performer decomposes into exactly 3 sentences."""
    top_asset = {"name": "Tesla Inc.", "price": "$218.80", "change_24h": 12.30}
    summary = compose_market_summary(top_asset)

    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s.strip()
    ]
    assert len(sentences) == 3

    assert "Tesla" in summary
    assert "12.3" in summary
    assert "$218.80" in summary
    assert validate_three_sentences(summary) is True


def test_validate_three_sentences_rejects_non_three():
    """Verify validator helper rejects summaries with 2 or 4 sentences."""
    two_sentences = "Tesla rallied 12.3% today. It is trading at $218.80."
    four_sentences = "Sentence one. Sentence two. Sentence three. Sentence four."
    assert validate_three_sentences(two_sentences) is False
    assert validate_three_sentences(four_sentences) is False


def test_launch_summary_editor_file_creation(tmp_path):
    """Verify summary editor writes text to file on disk."""
    summary_text = "Sentence one. Sentence two. Sentence three."
    out_file = tmp_path / "Market_Summary.txt"
    res = launch_summary_editor(summary_text, file_path=out_file, launch=False)

    assert res.exists()
    assert res.read_text(encoding="utf-8") == summary_text


# ---------------------------------------------------------------------------
# 5. Error Handling: Empty Data & Locked File
# ---------------------------------------------------------------------------


def test_empty_asset_data_raises_value_error(tmp_path):
    """Verify attempting to generate report with empty list raises ValueError."""
    with pytest.raises(ValueError, match="empty asset data"):
        create_market_report([], output_path=tmp_path / "Report.xlsx")


def test_locked_file_permission_error_handling(tmp_path, monkeypatch):
    """Verify locked file raises PermissionError and writes diagnostic log to logs/errors/."""
    monkeypatch.setenv("IRA_ERROR_LOG_DIR", str(tmp_path / "logs"))

    def broken_save(self, filename):
        raise PermissionError(f"[Errno 13] Permission denied: '{filename}'")

    monkeypatch.setattr(openpyxl.Workbook, "save", broken_save)

    with pytest.raises(PermissionError, match="Permission denied"):
        create_market_report(MOCK_ASSETS, output_path=tmp_path / "Locked_Report.xlsx")

    # Diagnostic log check
    error_logs = list((tmp_path / "logs").glob("error_*.log"))
    assert len(error_logs) >= 1
    log_content = error_logs[0].read_text(encoding="utf-8")
    assert "Permission denied" in log_content
    assert "create_market_report" in log_content


# ---------------------------------------------------------------------------
# 6. Assistant @function_tool Wiring & Integration
# ---------------------------------------------------------------------------


async def test_assistant_tools_include_market_tools():
    """Verify generate_market_report, create_styled_spreadsheet, and scrape_market_assets exist on Assistant."""
    assistant = Assistant()
    names = {tool.info.name for tool in assistant.tools}
    assert "generate_market_report" in names
    assert "create_styled_spreadsheet" in names
    assert "scrape_market_assets" in names
    assert len(names) == len(assistant.tools)


async def test_assistant_generate_market_report_success(tmp_path):
    """Verify Assistant.generate_market_report delegates and returns success message."""
    assistant = Assistant()
    out_file = tmp_path / "Market_Report.xlsx"

    with (
        patch("market_report.scrape_market_assets", return_value=MOCK_ASSETS),
        patch(
            "market_report.launch_summary_editor", return_value=tmp_path / "summary.txt"
        ),
    ):
        result = await assistant.generate_market_report(
            None, source_url="https://mock.test", output_path=str(out_file)
        )

    assert "Generated market report" in result
    assert "Tesla Inc" in result
    assert "+12.30%" in result
    assert out_file.exists()


async def test_assistant_create_styled_spreadsheet_tool(tmp_path):
    """Verify Assistant.create_styled_spreadsheet parses JSON and creates file."""
    assistant = Assistant()
    out_file = tmp_path / "Market_Report.xlsx"
    data_json = json.dumps(MOCK_ASSETS)

    result = await assistant.create_styled_spreadsheet(
        None, data_json=data_json, output_path=str(out_file)
    )

    assert "Created styled market report" in result
    assert out_file.exists()


async def test_assistant_create_styled_spreadsheet_empty_data_raises_tool_error():
    """Verify passing empty JSON array to create_styled_spreadsheet raises ToolError."""
    assistant = Assistant()
    with pytest.raises(ToolError, match="empty asset data"):
        await assistant.create_styled_spreadsheet(None, data_json="[]")


async def test_assistant_scrape_market_assets_tool():
    """Verify Assistant.scrape_market_assets returns JSON array of assets."""
    assistant = Assistant()
    with patch("market_report.scrape_market_assets", return_value=MOCK_ASSETS):
        result = await assistant.scrape_market_assets(None)
    data = json.loads(result)
    assert len(data) == 5
    assert data[0]["name"] == "NVIDIA Corporation"


# ---------------------------------------------------------------------------
# 7. Capabilities & Request Scope Integration
# ---------------------------------------------------------------------------


async def test_market_capabilities_loading():
    """Verify loading market capability succeeds and matches registered tools."""
    assistant = Assistant()
    assert "market" in CAPABILITIES
    result = await assistant.load_capabilities(None, "market")
    assert "generate_market_report" in result
    assert "create_styled_spreadsheet" in result
    assert "scrape_market_assets" in result


async def test_market_report_governed_under_request_scope(tmp_path):
    """Verify generate_market_report is governed as an effect by RequestScope."""
    from types import SimpleNamespace

    from request_scope_livekit import _OBSERVATIONS, RequestScopeTools

    # Verify classification: scrape is observation, generators are effects
    assert "scrape_market_assets" in _OBSERVATIONS
    assert "generate_market_report" not in _OBSERVATIONS
    assert "create_styled_spreadsheet" not in _OBSERVATIONS

    scope = RequestScope()
    facade = RequestScopeTools(scope)
    assistant = Assistant()
    tool_map = {t.info.name: t for t in assistant.tools}

    # Wrap tools under request scope facade
    wrapped = facade.wrap_tools([tool_map["generate_market_report"]])[0]
    request = scope.begin_request("Generate market report", source_id="test:1")
    handle = SimpleNamespace(id="h1", add_done_callback=lambda cb: None)
    facade.bind_speech(handle, request)

    # When scope is revoked, effect fails closed
    scope.revoke("User stopped task")
    with pytest.raises((ScopeViolation, ToolError)):
        ctx = SimpleNamespace(speech_handle=handle)
        await wrapped(ctx, output_path=str(tmp_path / "Revoked.xlsx"))
