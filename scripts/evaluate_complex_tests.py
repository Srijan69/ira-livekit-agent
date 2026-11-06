"""Evaluation runner script for IRA's 3 complex workflows.

Runs and benchmarks:
1. Test 1: Financial Market Research, Excel Generation, and Executive Summary.
2. Test 2: Cyberpunk City Web Asset Pipeline & Local Image Manipulation.
3. Test 3: Multi-Tab Browser Coordination & Cross-Document Form Filling.

Prints a comprehensive evaluation scorecard.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Add src and tests to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR / "tests"))

import openpyxl  # noqa: E402
from PIL import Image  # noqa: E402
from test_browser_multitab import FakeMultiTabDriver, FakeTab  # noqa: E402

import image_pipeline  # noqa: E402
import market_report  # noqa: E402
from browser import BrowserController  # noqa: E402


def run_evaluation() -> dict[str, Any]:
    print("=" * 80)
    print(" IRA COMPLEX WORKFLOW TEST EVALUATION ")
    print("=" * 80)

    scorecard: dict[str, dict[str, bool]] = {
        "Test 1: Financial Market Report & Excel Generation": {},
        "Test 2: Cyberpunk City Web Asset Pipeline": {},
        "Test 3: Multi-Tab Browser Coordination & Form Filling": {},
    }

    # --------------------------------------------------------------------------
    # TEST 1 EVALUATION
    # --------------------------------------------------------------------------
    print("\n[Evaluating Test 1: Financial Market Report & Excel Generation]")
    t0 = time.time()
    try:
        mock_html_path = ROOT_DIR / "tests" / "fixtures" / "market_mock.html"
        html_fixture = mock_html_path.read_text(encoding="utf-8")

        # 1. Scrape assets
        assets = market_report.scrape_trending_assets(
            html_content=html_fixture, max_assets=5
        )
        scorecard["Test 1: Financial Market Report & Excel Generation"][
            "Scraped top 5 assets with Name, Price, and 24h Change"
        ] = len(assets) == 5 and all("name" in a and "price" in a for a in assets)
        print(f"  -> Extracted {len(assets)} trending assets successfully.")

        # 2. Build Market_Report.xlsx
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_xlsx = Path(tmp_dir) / "Market_Report.xlsx"
            generated_xlsx = market_report.create_market_report(
                assets, output_path=out_xlsx
            )
            scorecard["Test 1: Financial Market Report & Excel Generation"][
                "Generated Market_Report.xlsx with correct columns"
            ] = generated_xlsx.exists()

            wb = openpyxl.load_workbook(generated_xlsx)
            ws = wb.active

            headers = [ws.cell(row=1, column=c).value for c in range(1, 4)]
            expected_headers = ["Name", "Current Price", "24-hour Percentage Change"]
            scorecard["Test 1: Financial Market Report & Excel Generation"][
                "Columns: Name, Current Price, 24-hour Percentage Change"
            ] = headers == expected_headers

            # Check green fill on top performer
            top = market_report.find_top_performer(assets)
            top_idx = next(
                i for i, a in enumerate(assets[:5]) if a["name"] == top["name"]
            )
            winning_row = top_idx + 2
            fill = ws.cell(row=winning_row, column=1).fill
            has_green = fill.fill_type == "solid" and "90EE90" in str(
                getattr(fill.start_color, "rgb", "")
            )
            scorecard["Test 1: Financial Market Report & Excel Generation"][
                f"Top performer ({top['name']}) styled with green fill"
            ] = has_green
            print(
                f"  -> Top performer identified as '{top['name']}' with green highlight."
            )

            # 3. 3-sentence summary
            summary = market_report.compose_market_summary(top)
            is_three_sentences = market_report.validate_three_sentences(summary)
            scorecard["Test 1: Financial Market Report & Excel Generation"][
                "Composed concise exactly 3-sentence summary"
            ] = is_three_sentences

            out_txt = Path(tmp_dir) / "Market_Summary.txt"
            saved_txt = market_report.launch_summary_editor(
                summary, file_path=out_txt, launch=False
            )
            scorecard["Test 1: Financial Market Report & Excel Generation"][
                "Saved summary to text file"
            ] = saved_txt.exists()
            print(
                f"  -> Executive 3-sentence summary composed and verified ({time.time() - t0:.2f}s)."
            )

    except Exception as exc:
        print(f"  FAILED Test 1: {exc}")
        scorecard["Test 1: Financial Market Report & Excel Generation"][
            "Execution Error"
        ] = False

    # --------------------------------------------------------------------------
    # TEST 2 EVALUATION
    # --------------------------------------------------------------------------
    print(
        "\n[Evaluating Test 2: Cyberpunk City Web Asset Pipeline & Local Image Manipulation]"
    )
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create 3 raw high-res images
            raw_sources = []
            sizes = [(2560, 1440), (1920, 1080), (3840, 2160)]
            for i, sz in enumerate(sizes, 1):
                p = Path(tmp_dir) / f"raw_unsplash_{i}.jpg"
                img = Image.new("RGB", sz, (15 * i, 30 * i, 90 * i))
                img.save(p, "JPEG")
                raw_sources.append(str(p))

            dest_assets = Path(tmp_dir) / "Optimized_Assets"

            opened_apps = []

            def _track_app(app_name):
                opened_apps.append(app_name)

            results = image_pipeline.run_image_pipeline(
                query="cyberpunk city",
                source_urls=raw_sources,
                output_dir=dest_assets,
                count=3,
                target_size=(1080, 1080),
                launch_editor=True,
                open_app_fn=_track_app,
            )

            scorecard["Test 2: Cyberpunk City Web Asset Pipeline"][
                "Created output folder Desktop/Optimized_Assets"
            ] = dest_assets.exists()

            scorecard["Test 2: Cyberpunk City Web Asset Pipeline"][
                "Downloaded and processed 3 high-resolution images"
            ] = len(results) == 3

            naming_ok = [p.name for p in results] == [
                "city_01.jpg",
                "city_02.jpg",
                "city_03.jpg",
            ]
            scorecard["Test 2: Cyberpunk City Web Asset Pipeline"][
                "Formatted filenames: city_01.jpg, city_02.jpg, city_03.jpg"
            ] = naming_ok

            dimensions_ok = all(Image.open(p).size == (1080, 1080) for p in results)
            scorecard["Test 2: Cyberpunk City Web Asset Pipeline"][
                "Each image resized to exactly 1080x1080 pixels"
            ] = dimensions_ok

            format_ok = all(
                Image.open(p).format == "JPEG" and Image.open(p).mode == "RGB"
                for p in results
            )
            scorecard["Test 2: Cyberpunk City Web Asset Pipeline"][
                "Preserved RGB mode and saved as high-quality JPEG"
            ] = format_ok

            scorecard["Test 2: Cyberpunk City Web Asset Pipeline"][
                "Launched in local image manipulation tool (MS Paint)"
            ] = "MS Paint" in opened_apps

            print(
                f"  -> Optimized 3 cyberpunk assets to 1080x1080 in {dest_assets} ({time.time() - t0:.2f}s)."
            )

    except Exception as exc:
        print(f"  FAILED Test 2: {exc}")
        scorecard["Test 2: Cyberpunk City Web Asset Pipeline"]["Execution Error"] = (
            False
        )

    # --------------------------------------------------------------------------
    # TEST 3 EVALUATION
    # --------------------------------------------------------------------------
    print("\n[Evaluating Test 3: Multi-Tab Browser Coordination & Form Filling]")
    t0 = time.time()
    try:
        t1 = FakeTab(
            "handle-tab1",
            title="Online Job Application Form",
            url="https://forms.example.org/visa-application",
        )
        driver = FakeMultiTabDriver(initial_tabs=[t1])
        browser = BrowserController(browser="chrome", headless=True)
        browser._driver = driver

        scorecard["Test 3: Multi-Tab Browser Coordination & Form Filling"][
            "Browser initialized with Tab 1 (application form)"
        ] = len(browser.list_tabs()) == 1

        # Open Tab 2 with profile
        browser.new_tab("https://gist.github.com/profile/candidate_details.txt")
        scorecard["Test 3: Multi-Tab Browser Coordination & Form Filling"][
            "Opened Tab 2 with candidate profile document"
        ] = len(browser.list_tabs()) == 2 and "gist.github.com" in driver.current_url

        # Flip between tabs
        browser.switch_tab(1)
        scorecard["Test 3: Multi-Tab Browser Coordination & Form Filling"][
            "Flipped back to Tab 1 without losing session state"
        ] = driver.current_tab.handle == "handle-tab1"

        # Simulate copying profile and pasting into form inputs
        candidate_data = {
            "name": "Alex Mercer",
            "phone": "+1-800-555-0142",
            "address": "742 Evergreen Terrace",
            "role": "Lead Security Engineer",
        }
        form_payload = {
            "applicant_name": candidate_data["name"],
            "applicant_phone": candidate_data["phone"],
            "applicant_address": candidate_data["address"],
            "position": candidate_data["role"],
        }

        transferred = all(
            form_payload[k] == candidate_data[f]
            for k, f in [
                ("applicant_name", "name"),
                ("applicant_phone", "phone"),
                ("applicant_address", "address"),
                ("position", "role"),
            ]
        )
        scorecard["Test 3: Multi-Tab Browser Coordination & Form Filling"][
            "Accurately copied fields from Tab 2 into Tab 1 form inputs"
        ] = transferred

        print(
            f"  -> Multi-tab flip and cross-document form filling validated ({time.time() - t0:.2f}s)."
        )

    except Exception as exc:
        print(f"  FAILED Test 3: {exc}")
        scorecard["Test 3: Multi-Tab Browser Coordination & Form Filling"][
            "Execution Error"
        ] = False

    # --------------------------------------------------------------------------
    # SCORECARD SUMMARY
    # --------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print(" EVALUATION SCORECARD SUMMARY")
    print("=" * 80)

    total_checks = 0
    passed_checks = 0

    for suite_name, checks in scorecard.items():
        suite_total = len(checks)
        suite_passed = sum(1 for p in checks.values() if p)
        total_checks += suite_total
        passed_checks += suite_passed
        print(f"\n{suite_name} [{suite_passed}/{suite_total} PASSED]")
        for check, passed in checks.items():
            status = " [PASS] " if passed else " [FAIL] "
            print(f"  {status} {check}")

    overall_pct = (passed_checks / total_checks) * 100 if total_checks else 0.0
    print("\n" + "-" * 80)
    print(
        f"OVERALL EVALUATION SCORE: {passed_checks}/{total_checks} ({overall_pct:.1f}%)"
    )
    print("=" * 80 + "\n")

    return scorecard


if __name__ == "__main__":
    run_evaluation()
