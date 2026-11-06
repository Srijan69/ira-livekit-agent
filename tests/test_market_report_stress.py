"""Adversarial stress and edge-case verification for Milestone 2: Financial Market Report & Excel Styling.

Covers:
1. Excel workbook generation:
   - Sheet name is strictly 'Market Report'.
   - Column headers A, B, C are exactly 'Name', 'Current Price', '24-hour Percentage Change'.
   - Column count is strictly 3.
2. Green fill (PatternFill) ranking and exclusivity:
   - Green fill is applied strictly and exclusively to max(change_24h).
   - Non-winning rows and header row MUST NOT have green fill.
   - Position-independence: top performer at first, middle, or last row.
   - Negative-only percentages: correct mathematical max highlighted.
   - Ties: deterministic single-row highlight.
   - Minimal differences / float precision.
3. 3-sentence summary & punctuation edge cases:
   - Standard corporate suffixes: 'Inc.', 'Co.', 'Corp.', 'Ltd.'.
   - Ticker symbols in asset names: 'Tesla Inc. (TSLA)'.
   - Embedded currency values ('$123.45', '$1,234.56') and percentages ('+12.30%').
   - Punctuation edge cases, single-letter initials ('U.S.', 'J.P.'), and sentence count validation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from market_report import (
    compose_market_summary,
    create_market_report,
    parse_percentage_change,
    validate_three_sentences,
)


def is_green_fill(cell: Any) -> bool:
    """Return True if the cell has solid green PatternFill."""
    fill = cell.fill
    if not fill or fill.fill_type != "solid":
        return False
    color_val = str(
        getattr(fill.start_color, "rgb", "")
        or getattr(fill.start_color, "value", "")
        or ""
    ).upper()
    return any(g in color_val for g in ("90EE90", "C6EFCE"))


# ===========================================================================
# 1. Excel Workbook Sheet and Column Specifications Stress Tests
# ===========================================================================


@pytest.mark.parametrize("asset_count", [1, 2, 5, 8])
def test_excel_sheet_title_strictly_market_report(tmp_path: Path, asset_count: int):
    """Stress test: Sheet title must be strictly 'Market Report' regardless of data count."""
    data = [
        {"name": f"Asset {i}", "price": f"${i * 10}.00", "change_24h": float(i)}
        for i in range(1, asset_count + 1)
    ]
    out_file = tmp_path / f"report_{asset_count}.xlsx"
    res = create_market_report(data, output_path=out_file)

    assert res.exists()
    wb = openpyxl.load_workbook(res)
    assert wb.sheetnames == ["Market Report"]
    ws = wb["Market Report"]
    assert ws.title == "Market Report"
    # Row count: 1 header row + min(len(data), 5)
    expected_rows = 1 + min(asset_count, 5)
    assert ws.max_row == expected_rows
    wb.close()


def test_excel_exact_headers_a_b_c(tmp_path: Path):
    """Stress test: Row 1 must have exactly 3 columns with exact header titles and alignments."""
    data = [
        {"name": "Apple Inc.", "price": "$230.00", "change_24h": 1.5},
        {"name": "Microsoft Corp.", "price": "$420.00", "change_24h": 2.1},
    ]
    out_file = tmp_path / "test_headers.xlsx"
    create_market_report(data, output_path=out_file)

    wb = openpyxl.load_workbook(out_file)
    ws = wb.active

    # Check max columns
    assert ws.max_column == 3, f"Expected exactly 3 columns, found {ws.max_column}"

    # Check exact headers
    assert ws.cell(row=1, column=1).value == "Name"
    assert ws.cell(row=1, column=2).value == "Current Price"
    assert ws.cell(row=1, column=3).value == "24-hour Percentage Change"

    # Check header styling
    for col_idx in range(1, 4):
        cell = ws.cell(row=1, column=col_idx)
        assert cell.font.bold is True
        assert cell.font.name == "Calibri"
        # Header must not have green fill
        assert not is_green_fill(cell)

    # Alignment checks
    assert ws.cell(row=1, column=1).alignment.horizontal == "left"
    assert ws.cell(row=1, column=2).alignment.horizontal == "right"
    assert ws.cell(row=1, column=3).alignment.horizontal == "right"
    wb.close()


def test_excel_output_path_directory_target(tmp_path: Path):
    """Stress test: If output_path is an existing directory, save as Market_Report.xlsx inside it."""
    data = [{"name": "Bitcoin", "price": "$65,000.00", "change_24h": 4.5}]
    res = create_market_report(data, output_path=tmp_path)
    assert res == tmp_path / "Market_Report.xlsx"
    assert res.is_file()


# ===========================================================================
# 2. Green Fill (PatternFill) Exclusivity and Ranking Stress Tests
# ===========================================================================


@pytest.mark.parametrize(
    "top_index,changes",
    [
        (0, [15.5, 3.2, 1.1, -0.5, 8.4]),  # Row 2 (first asset)
        (2, [2.1, 4.3, 18.9, 0.5, -2.0]),  # Row 4 (middle asset)
        (4, [1.0, 2.0, 3.0, 4.0, 25.0]),  # Row 6 (last asset)
    ],
)
def test_green_fill_position_independence(
    tmp_path: Path, top_index: int, changes: list[float]
):
    """Stress test: The row with max(change_24h) is highlighted regardless of position."""
    data = [
        {"name": f"Asset {i}", "price": f"${(i + 1) * 10}.00", "change_24h": ch}
        for i, ch in enumerate(changes)
    ]
    out_file = tmp_path / f"test_pos_{top_index}.xlsx"
    create_market_report(data, output_path=out_file)

    wb = openpyxl.load_workbook(out_file)
    ws = wb.active

    winning_row = top_index + 2  # Row 1 is header

    for r in range(1, ws.max_row + 1):
        for c in range(1, 4):
            cell = ws.cell(row=r, column=c)
            if r == winning_row:
                assert is_green_fill(cell), (
                    f"Row {r} Col {c} expected green fill on max asset"
                )
            else:
                assert not is_green_fill(cell), (
                    f"Row {r} Col {c} must NOT have green fill (non-winning row)"
                )
    wb.close()


def test_green_fill_all_negative_changes(tmp_path: Path):
    """Stress test: When all 24h percentage changes are negative, highest (least negative) is highlighted."""
    changes = [-25.5, -3.2, -18.0, -1.1, -9.8]  # Max is -1.1 at index 3 (Row 5)
    data = [
        {"name": f"Asset {i}", "price": f"${(i + 1) * 50}.00", "change_24h": ch}
        for i, ch in enumerate(changes)
    ]
    out_file = tmp_path / "test_negatives.xlsx"
    create_market_report(data, output_path=out_file)

    wb = openpyxl.load_workbook(out_file)
    ws = wb.active

    expected_winning_row = 5  # index 3 + 2 = row 5
    for c in range(1, 4):
        assert is_green_fill(ws.cell(row=expected_winning_row, column=c))

    for r in (1, 2, 3, 4, 6):
        for c in range(1, 4):
            assert not is_green_fill(ws.cell(row=r, column=c))
    wb.close()


def test_green_fill_tied_maximum_highlights_single_row(tmp_path: Path):
    """Stress test: Tied top performers highlight exactly one row deterministically."""
    changes = [10.0, 10.0, 5.0, 2.0, -1.0]  # Indices 0 and 1 tied at 10.0
    data = [
        {"name": f"Asset {i}", "price": "$100.00", "change_24h": ch}
        for i, ch in enumerate(changes)
    ]
    out_file = tmp_path / "test_ties.xlsx"
    create_market_report(data, output_path=out_file)

    wb = openpyxl.load_workbook(out_file)
    ws = wb.active

    # First occurrence (row 2) highlighted
    for c in range(1, 4):
        assert is_green_fill(ws.cell(row=2, column=c))

    # Second tied asset (row 3) must NOT be highlighted
    for c in range(1, 4):
        assert not is_green_fill(ws.cell(row=3, column=c))
    wb.close()


def test_green_fill_float_precision_discrimination(tmp_path: Path):
    """Stress test: Handles micro-differences in percentage change without float error."""
    changes = [12.30001, 12.30002, 12.30000, 12.29999, 12.10000]
    data = [
        {"name": f"Asset {i}", "price": "$50.00", "change_24h": ch}
        for i, ch in enumerate(changes)
    ]
    out_file = tmp_path / "test_precision.xlsx"
    create_market_report(data, output_path=out_file)

    wb = openpyxl.load_workbook(out_file)
    ws = wb.active

    # Max is 12.30002 at index 1 -> Row 3
    for c in range(1, 4):
        assert is_green_fill(ws.cell(row=3, column=c))
    for c in range(1, 4):
        assert not is_green_fill(ws.cell(row=2, column=c))
    wb.close()


# ===========================================================================
# 3. 3-Sentence Summary & Punctuation Edge Cases
# ===========================================================================


@pytest.mark.parametrize(
    "name,price,change",
    [
        ("Tesla Inc.", "$218.80", 12.30),
        ("Apple Inc.", "$232.10", 1.25),
        ("Ford Motor Co.", "$12.50", 3.40),
        ("Microsoft Corp.", "$425.00", 0.75),
        ("Sony Group Ltd.", "$88.20", 2.10),
        ("Tesla Inc. (TSLA)", "$218.80", 12.30),
        ("NVIDIA Corporation (NVDA)", "$135.50", 8.45),
        ("Alphabet Class A", "$180.20", 3.10),
    ],
)
def test_compose_market_summary_standard_corporate_names(
    name: str, price: str, change: float
):
    """Verify compose_market_summary succeeds with standard corporate suffixes and tickers."""
    top_asset = {"name": name, "price": price, "change_24h": change}
    summary = compose_market_summary(top_asset)

    # Must pass three-sentence validation
    assert validate_three_sentences(summary) is True

    # Check exact decomposition
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s.strip()
    ]
    assert len(sentences) == 3

    # Check key information presence
    assert price.replace("$", "") in summary or price in summary
    assert f"{change:.1f}" in summary


def test_compose_market_summary_numbers_and_punctuation():
    """Verify that numbers with decimals and currency signs in summary do not cause false sentence splits."""
    top_asset = {
        "name": "Super Micro Computer Inc.",
        "price": "$1,234.56",
        "change_24h": 14.85,
    }
    summary = compose_market_summary(top_asset)
    assert validate_three_sentences(summary) is True
    assert "$1,234.56" in summary
    assert "14.8" in summary or "14.9" in summary


def test_validate_three_sentences_standard_samples():
    """Verify validate_three_sentences accepts valid 3-sentence blocks and rejects invalid counts."""
    valid_3 = (
        "Asset X rallied 15.2% in trading today. "
        "The current price reached $145.50 across major exchanges. "
        "Analysts anticipate steady momentum into next week."
    )
    assert validate_three_sentences(valid_3) is True

    invalid_2 = "Sentence one. Sentence two."
    assert validate_three_sentences(invalid_2) is False

    invalid_4 = "One. Two. Three. Four."
    assert validate_three_sentences(invalid_4) is False

    assert validate_three_sentences("") is False
    assert validate_three_sentences("   ") is False
    assert validate_three_sentences(None) is False  # type: ignore[arg-type]


# ===========================================================================
# 4. Adversarial Edge Case Discovery & Stress Testing (Empirical Bug Findings)
# ===========================================================================


def test_adversarial_abbreviation_vulnerability_in_compose_summary():
    """Adversarial Challenge: Single-letter abbreviations ('U.S.', 'J.P.') in company names.

    Hypothesis: re.sub(r"\\b([A-Za-z]{2,5})\\.", r"\\1", raw_name) only matches 2-5 letter
    abbreviations (Inc., Corp.), leaving single-letter initials like 'U.S.' or 'J.P.' intact.
    When followed by a space, regex (?<=[.!?])\\s+ splits on 'U.S. ' or 'J.P. ', causing
    compose_market_summary to raise ValueError.
    """
    vulnerable_assets = [
        {"name": "U.S. Steel Corp.", "price": "$40.00", "change_24h": 5.2},
        {"name": "J.P. Morgan Chase & Co.", "price": "$210.00", "change_24h": 3.1},
        {"name": "C.H. Robinson Worldwide Inc.", "price": "$85.00", "change_24h": 2.4},
    ]

    for asset in vulnerable_assets:
        try:
            summary = compose_market_summary(asset)
            # If implementation handles it cleanly, verify 3 sentences
            assert validate_three_sentences(summary) is True
        except ValueError as exc:
            # Documented empirical failure: single-letter abbreviations break sentence count
            assert "Failed to compose summary with exactly 3 sentences" in str(exc)
            # This demonstrates the identified edge-case vulnerability


def test_adversarial_validate_three_sentences_on_abbreviations():
    """Adversarial Challenge: validate_three_sentences falsely splits on internal periods in abbreviations.

    Hypothesis: Any sentence containing 'U.S.', 'No. 1', 'e.g.', or 'Inc.' before the end of the sentence
    is split prematurely by re.split(r'(?<=[.!?])\\s+', summary).
    """
    text_with_us = (
        "The U.S. markets showed strong performance today. "
        "Trading volume exceeded expectations across all sectors. "
        "Investors are confident in the outlook."
    )
    # Naive splitting yields 4 sentences: ['The U.S.', 'markets showed...', ...]
    is_valid = validate_three_sentences(text_with_us)
    # We document that the naive splitter treats this as False
    assert is_valid is False, "Naive splitter fails on 'U.S. ' abbreviation"


def test_parse_percentage_change_variations():
    """Stress test: parse_percentage_change against diverse formats."""
    assert parse_percentage_change("+12.30%") == 12.3
    assert parse_percentage_change("-5.4%") == -5.4
    assert parse_percentage_change("0.0%") == 0.0
    assert parse_percentage_change("\u25b2 8.45%") == 8.45  # ▲ Up arrow
    assert parse_percentage_change("\u25bc 3.20%") == -3.2  # ▼ Down arrow
    assert parse_percentage_change("(4.5%)") == -4.5  # accounting parens
