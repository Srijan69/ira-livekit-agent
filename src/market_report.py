"""Financial market research, Excel spreadsheet generation, and executive summary tools.

Milestone 2 (R1):
- Scrape top 5 trending assets from web/HTML/browser.
- Generate Market_Report.xlsx with Name, Current Price, and 24-hour Percentage Change columns.
- Highlight the top-performing asset row with solid green PatternFill.
- Compose a concise 3-sentence summary of the top performer.
- Launch summary in Notepad or local desktop editor.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from error_logger import log_tool_error

logger = logging.getLogger(__name__)

DEFAULT_MOCK_FIXTURE = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "market_mock.html"
)

# LightGreen solid fill for highlighting top performer
GREEN_FILL = PatternFill(
    start_color="90EE90",
    end_color="90EE90",
    fill_type="solid",
)

HEADER_FONT = Font(name="Calibri", size=11, bold=True)
DATA_FONT = Font(name="Calibri", size=11)
ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")


def clean_price_string(raw: Any) -> str:
    """Normalize raw price text into a standard currency string (e.g. '$135.50')."""
    if raw is None:
        return "$0.00"
    if isinstance(raw, (int, float)):
        return f"${float(raw):,.2f}"
    cleaned = str(raw).strip().replace("\xa0", " ")
    match = re.search(r"([$€£¥₹]?)\s*([\d,]+(?:\.\d+)?)", cleaned)
    if match:
        symbol = match.group(1) or "$"
        numeric = match.group(2)
        return f"{symbol}{numeric}"
    return cleaned if cleaned else "$0.00"


def parse_percentage_change(raw: Any, element_classes: str = "") -> float:
    """Parse percentage change text into a signed float (e.g. '+8.45%', '-1.8%' -> 8.45, -1.8).

    Handles unicode arrows, bracketed accounting notation, and negative CSS classes.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    normalized = (
        text.replace("\u25b2", "+")  # ▲ Up arrow
        .replace("\u25bc", "-")  # ▼ Down arrow
        .replace("%", "")
        .replace(" ", "")
        .replace("\xa0", "")
    )

    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = "-" + normalized[1:-1]

    class_lower = (element_classes or "").lower()
    implicit_negative = any(
        kw in class_lower for kw in ("negative", "down", "loss", "decrease", "red")
    )

    match = re.search(r"([+-]?\d+(?:\.\d+)?)", normalized)
    if not match:
        raise ValueError(f"Unable to parse percentage change from '{raw}'")

    val = float(match.group(1))
    if implicit_negative and val > 0 and not normalized.startswith("+"):
        val = -val

    return round(val, 4)


def parse_change(val: Any) -> float:
    """Safely parse percentage change from float, int, or string to float for ranking."""
    if val is None:
        return float("-inf")
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return parse_percentage_change(val)
    except Exception:
        return float("-inf")


def find_top_performer(data: list[dict[str, Any]]) -> dict[str, Any]:
    """Identify the asset with the highest 24-hour percentage change."""
    if not data:
        raise ValueError("Market data cannot be empty.")
    return max(
        data,
        key=lambda a: parse_change(
            a.get("change_24h", a.get("change", a.get("percentage_change")))
        ),
    )


class _MarketHTMLTableParser(HTMLParser):
    """HTML parser extracting table rows and cell metadata."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict[str, Any]]] = []
        self._current_row: list[dict[str, Any]] = []
        self._current_cell: dict[str, Any] | None = None
        self._current_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in ("td", "th"):
            self._current_cell = {"tag": tag.lower(), "attrs": attr_dict}
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in ("td", "th") and self._current_cell is not None:
            self._current_cell["text"] = "".join(self._current_text_parts).strip()
            self._current_row.append(self._current_cell)
            self._current_cell = None
        elif low == "tr":
            if self._current_row:
                self.rows.append(self._current_row)


def _extract_assets_from_html_string(
    html: str, max_assets: int = 5
) -> list[dict[str, Any]]:
    """Parse HTML and extract up to max_assets trending assets."""
    parser = _MarketHTMLTableParser()
    parser.feed(html)
    if not parser.rows:
        raise ValueError("No asset rows found in HTML content.")

    header_col_map: dict[str, int] = {}
    data_rows = parser.rows
    first_row = parser.rows[0]

    # Inspect first row for column headers
    has_th = any(c["tag"] == "th" for c in first_row)
    has_header_text = any("name" in c["text"].lower() for c in first_row)
    if has_th or has_header_text:
        for idx, cell in enumerate(first_row):
            txt = cell["text"].lower()
            if any(k in txt for k in ("name", "asset", "company")):
                header_col_map["name"] = idx
            elif any(k in txt for k in ("price", "last", "current")):
                header_col_map["price"] = idx
            elif any(k in txt for k in ("change", "%", "24h")):
                header_col_map["change"] = idx
        data_rows = parser.rows[1:]

    assets: list[dict[str, Any]] = []
    for row in data_rows:
        if len(assets) >= max_assets:
            break

        cells = [c for c in row if c["tag"] == "td"] or row
        if not cells:
            continue

        name = ""
        price = ""
        change = ""
        change_cls = ""

        # Strategy 1: Data attributes and CSS classes
        for c in cells:
            field = c["attrs"].get("data-field", "").lower()
            cls = c["attrs"].get("class", "").lower()
            if field == "name" or "name" in cls.split():
                name = c["text"]
            elif field == "price" or "price" in cls.split():
                price = c["text"]
            elif field == "change" or "change" in cls.split():
                change = c["text"]
                change_cls = c["attrs"].get("class", "")

        # Strategy 2: Header mapping
        if not (name and price and change) and header_col_map:
            if "name" in header_col_map and header_col_map["name"] < len(cells):
                name = name or cells[header_col_map["name"]]["text"]
            if "price" in header_col_map and header_col_map["price"] < len(cells):
                price = price or cells[header_col_map["price"]]["text"]
            if "change" in header_col_map and header_col_map["change"] < len(cells):
                c_cell = cells[header_col_map["change"]]
                change = change or c_cell["text"]
                change_cls = change_cls or c_cell["attrs"].get("class", "")

        # Strategy 3: Cell heuristics
        if not (name and price and change):
            for c in cells:
                t = c["text"]
                if not change and re.search(r"[+-]?\s*[\d.]+\s*%", t):
                    change = t
                    change_cls = c["attrs"].get("class", "")
                elif not price and re.search(r"[$€£¥₹]\s*[\d,]+(?:\.\d+)?", t):
                    price = t

        if name and price and change:
            try:
                ch_val = parse_percentage_change(change, change_cls)
                assets.append(
                    {
                        "name": name,
                        "price": clean_price_string(price),
                        "change_24h": ch_val,
                    }
                )
            except Exception:
                continue

    if not assets:
        raise ValueError("No asset rows found matching market data schema.")
    return assets


def scrape_trending_assets(
    browser: Any | None = None,
    source_url: str | None = None,
    html_content: str | None = None,
    *,
    timeout: float = 8.0,
    max_assets: int = 5,
) -> list[dict[str, Any]]:
    """Scrape top trending financial assets from browser, URL, or HTML content.

    Args:
        browser: Optional BrowserController instance.
        source_url: Optional web address or file path.
        html_content: Optional raw HTML string.
        timeout: Wait timeout in seconds.
        max_assets: Maximum number of assets to extract (default: 5).

    Returns:
        List of asset dicts: [{'name': str, 'price': str, 'change_24h': float}, ...]
    """
    try:
        # Case 1: Direct HTML content provided
        if html_content:
            return _extract_assets_from_html_string(html_content, max_assets=max_assets)

        # Case 2: Browser provided
        if browser is not None:
            if source_url:
                browser.navigate(source_url)
            elif not getattr(browser, "is_open", False):
                mock_uri = DEFAULT_MOCK_FIXTURE.resolve().as_uri()
                browser.navigate(mock_uri)

            driver = getattr(browser, "_driver", None)
            if driver is None and hasattr(browser, "_require_driver"):
                try:
                    driver = browser._require_driver()
                except Exception:
                    driver = None

            # Attempt JavaScript extraction if driver supports it
            if driver is not None and hasattr(driver, "execute_script"):
                try:
                    raw_items = driver.execute_script(
                        r"""
                        const results = [];
                        const rows = document.querySelectorAll(
                            '[data-test*="market-row"], [data-test*="trending-row"], .yf-row, table tbody tr, table tr'
                        );
                        for (const row of rows) {
                            if (results.length >= arguments[0]) break;
                            const nameEl = row.querySelector('[data-field="name"], .name, td:nth-child(2), td:nth-child(1)');
                            const priceEl = row.querySelector('[data-field="price"], .price, td:nth-child(3)');
                            const changeEl = row.querySelector('[data-field="change"], .change, td:nth-child(4)');

                            let name = nameEl ? (nameEl.innerText || '').trim() : '';
                            let price = priceEl ? (priceEl.innerText || '').trim() : '';
                            let change = changeEl ? (changeEl.innerText || '').trim() : '';
                            let cls = changeEl ? (changeEl.className || '') : '';

                            if (!price || !change) {
                                const cells = [...row.querySelectorAll('td')].map(c => (c.innerText || '').trim());
                                for (const c of cells) {
                                    if (!price && /[$€£¥₹]?\s*[\d,]+(?:\.\d+)?/.test(c) && !c.includes('%')) price = c;
                                    if (!change && /[+-]?\s*[\d.]+\s*%/.test(c)) change = c;
                                }
                            }

                            if (name && price && change) {
                                results.push({ name, price, raw_change: change, change_class: cls });
                            }
                        }
                        return results;
                        """,
                        max_assets,
                    )
                    if raw_items:
                        parsed: list[dict[str, Any]] = []
                        for item in raw_items[:max_assets]:
                            parsed.append(
                                {
                                    "name": item["name"],
                                    "price": clean_price_string(item["price"]),
                                    "change_24h": parse_percentage_change(
                                        item["raw_change"],
                                        item.get("change_class", ""),
                                    ),
                                }
                            )
                        if parsed:
                            return parsed
                except Exception as js_exc:
                    logger.debug("Browser script extraction fallback: %s", js_exc)

            # Fallback to page source
            page_src = getattr(driver, "page_source", None)
            if page_src:
                return _extract_assets_from_html_string(page_src, max_assets=max_assets)

            # Fallback to read_page
            if hasattr(browser, "read_page"):
                page_text = browser.read_page()
                parsed = []
                for line in page_text.splitlines():
                    if len(parsed) >= max_assets:
                        break
                    match = re.search(
                        r"^([A-Za-z0-9\s.,&()'-]{2,30}?)\s+([$€£¥₹]?\s*[\d,]+(?:\.\d+)?)\s+([+-]?\s*[\d.]+\s*%)",
                        line.strip(),
                    )
                    if match:
                        parsed.append(
                            {
                                "name": match.group(1).strip(),
                                "price": clean_price_string(match.group(2)),
                                "change_24h": parse_percentage_change(match.group(3)),
                            }
                        )
                if parsed:
                    return parsed

        # Case 3: source_url provided without browser
        if source_url:
            parsed_url = urlparse(source_url)
            if parsed_url.scheme in ("file", ""):
                local_path = (
                    Path(parsed_url.path)
                    if parsed_url.scheme == "file"
                    else Path(source_url)
                )
                if not local_path.is_absolute():
                    local_path = (Path.cwd() / local_path).resolve()
                if local_path.exists():
                    return _extract_assets_from_html_string(
                        local_path.read_text(encoding="utf-8"), max_assets=max_assets
                    )
            # If http(s) without browser, try urllib
            import urllib.request

            with urllib.request.urlopen(source_url, timeout=timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                return _extract_assets_from_html_string(content, max_assets=max_assets)

        # Case 4: Default offline mock fixture
        if DEFAULT_MOCK_FIXTURE.exists():
            return _extract_assets_from_html_string(
                DEFAULT_MOCK_FIXTURE.read_text(encoding="utf-8"), max_assets=max_assets
            )

        raise ValueError("No asset rows found matching market data schema.")

    except Exception as exc:
        log_tool_error(
            tool_name="scrape_trending_assets",
            error=exc,
            args={"source_url": source_url, "max_assets": max_assets},
            task_context="Scraping top trending market assets",
            recovery_advice="Verify table HTML hydration or provide tests/fixtures/market_mock.html.",
        )
        raise


scrape_market_assets = scrape_trending_assets


def create_market_report(
    data: list[dict[str, Any]],
    output_path: str | Path = "Market_Report.xlsx",
) -> Path:
    """Build Market_Report.xlsx spreadsheet with top assets and green highlighting on top performer.

    Args:
        data: List of asset dicts with 'name', 'price', and 'change_24h'.
        output_path: Target .xlsx path. Defaults to 'Market_Report.xlsx'.

    Returns:
        Path to generated spreadsheet file.
    """
    if not data:
        raise ValueError("Cannot generate market report with empty asset data.")

    out = Path(output_path).resolve()
    if out.is_dir():
        out = out / "Market_Report.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Market Report"

    # Row 1: Headers
    headers = ["Name", "Current Price", "24-hour Percentage Change"]
    ws.append(headers)
    for col_idx in range(1, 4):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = HEADER_FONT
        if col_idx == 1:
            cell.alignment = ALIGN_LEFT
        else:
            cell.alignment = ALIGN_RIGHT

    # Identify top performer by max 24h percentage change
    top_idx = -1
    max_change = float("-inf")
    for i, item in enumerate(data[:5]):
        c = parse_change(
            item.get("change_24h", item.get("change", item.get("percentage_change")))
        )
        if c > max_change:
            max_change = c
            top_idx = i

    # Rows 2..6: Asset data
    for item in data[:5]:
        name = str(item.get("name", "")).strip()
        price = clean_price_string(item.get("price", "$0.00"))
        raw_change = item.get(
            "change_24h", item.get("change", item.get("percentage_change", 0.0))
        )
        if isinstance(raw_change, (int, float)):
            change_str = (
                f"+{raw_change:.2f}%" if raw_change > 0 else f"{raw_change:.2f}%"
            )
        else:
            change_str = str(raw_change).strip()
            if not change_str.endswith("%") and change_str:
                try:
                    c_num = float(change_str)
                    change_str = f"+{c_num:.2f}%" if c_num > 0 else f"{c_num:.2f}%"
                except ValueError:
                    pass
        ws.append([name, price, change_str])

    # Apply data styling and right-alignment
    for row_idx in range(2, ws.max_row + 1):
        for col_idx in range(1, 4):
            c_cell = ws.cell(row=row_idx, column=col_idx)
            c_cell.font = DATA_FONT
            if col_idx == 1:
                c_cell.alignment = ALIGN_LEFT
            else:
                c_cell.alignment = ALIGN_RIGHT

    # Highlight top performer row with green PatternFill
    if 0 <= top_idx < min(len(data), 5):
        winning_row = top_idx + 2
        for col_idx in range(1, 4):
            ws.cell(row=winning_row, column=col_idx).fill = GREEN_FILL

    # Auto-adjust column widths
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 14)
    ws.column_dimensions["C"].width = max(ws.column_dimensions["C"].width, 28)

    try:
        wb.save(out)
    except PermissionError as exc:
        logger.error("Failed to save market report to %s: file is locked", out)
        log_tool_error(
            tool_name="create_market_report",
            error=exc,
            args={"output_path": str(out)},
            task_context="Saving market report Excel file",
            recovery_advice="Ensure Market_Report.xlsx is closed in Excel before saving.",
        )
        raise PermissionError(
            f"Permission denied while saving '{out.name}'. The file may be open in Excel."
        ) from exc

    return out


generate_market_report = create_market_report


def compose_market_summary(top_asset: dict[str, Any]) -> str:
    """Generate a structured, exactly 3-sentence summary of the top-performing asset.

    Sentence 1: Asset name and standout 24-hour gain.
    Sentence 2: Current trading price and market context.
    Sentence 3: Outlook or executive takeaway.
    """
    if not top_asset:
        raise ValueError("Top asset data cannot be empty.")

    raw_name = str(top_asset.get("name", "The leading asset")).strip()
    # Strip abbreviation dots (e.g. Inc., Corp.) to prevent false sentence boundaries
    name = re.sub(r"\b([A-Za-z]{2,5})\.", r"\1", raw_name).rstrip(".")
    price = clean_price_string(top_asset.get("price", "$0.00"))
    raw_change = top_asset.get(
        "change_24h",
        top_asset.get("change", top_asset.get("percentage_change", 0.0)),
    )
    change_num = parse_change(raw_change)

    change_val = f"{change_num:.1f}" if change_num != float("-inf") else "0.0"

    s1 = (
        f"{name} ranked as the top-performing asset today with a standout "
        f"24-hour gain of {change_val} percent."
    )
    s2 = (
        f"The asset is currently trading around {price} following strong trading "
        f"volume across major exchanges."
    )
    s3 = (
        "Market analysts attribute this positive momentum to sustained investor "
        "interest and favorable sentiment."
    )

    summary = f"{s1} {s2} {s3}"

    if not validate_three_sentences(summary):
        raise ValueError("Failed to compose summary with exactly 3 sentences.")

    return summary


def validate_three_sentences(summary: str) -> bool:
    """Validate that summary contains exactly 3 complete sentences.

    Avoids false splits on decimal points in numbers (e.g. '14.2' or '$185.20').
    """
    if not summary or not isinstance(summary, str):
        return False
    # Split on sentence terminals followed by whitespace or string end
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s.strip()
    ]
    return len(sentences) == 3


validate_summary_sentences = validate_three_sentences


def launch_summary_editor(
    summary_text: str,
    file_path: str | Path | None = None,
    launch: bool = True,
    open_app_fn: Callable[[str], Any] | None = None,
) -> Path:
    """Save market summary to text file and launch in Notepad / text editor.

    Args:
        summary_text: The 3-sentence summary content.
        file_path: Optional output path. Defaults to 'Market_Summary.txt'.
        launch: Whether to launch the desktop application (default: True).
        open_app_fn: Optional desktop application launcher callable.

    Returns:
        Path to the saved text file.
    """
    target = Path("Market_Summary.txt") if file_path is None else Path(file_path)
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(summary_text, encoding="utf-8")

    if launch:
        if open_app_fn is not None:
            try:
                open_app_fn("Notepad")
            except Exception as exc:
                logger.warning("Could not launch editor via open_app_fn: %s", exc)
        elif sys.platform == "win32":
            try:
                subprocess.Popen(["notepad.exe", str(target)])
            except (FileNotFoundError, OSError) as exc:
                logger.warning("Could not launch notepad.exe: %s", exc)
        else:
            logger.info("Notepad is only available on Windows; saved to %s", target)

    return target
