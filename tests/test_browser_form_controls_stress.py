"""Stress tests and empirical challenge suite for form controls and dropdown selection in src/browser.py.

Exercises:
1. Date inputs: leap years, century leaps, malformed dates, empty strings, past/future boundaries.
2. Datetime-local and month inputs: standard formats, boundaries, malformed inputs.
3. Select dropdowns: duplicate labels, special characters, whitespace, empty options, case mismatches.
4. Attempting select_dropdown on non-select elements (div, button, input, anchor).
5. Attempting type_text on a <select> element (rejection & guidance).
6. Disabled options and disabled select elements.
"""

from __future__ import annotations

import pytest

from browser import BrowserController
from tests.fixtures.playwright_fakes import (
    FakeElement,
    FakeOption,
    FakePage,
    FakeSelectElement,
    controller_with,
)

StressFakeOption = FakeOption
StressFakeSelectElement = FakeSelectElement

# ===========================================================================
# 1. Date Inputs: Boundary & Malformed Tests
# ===========================================================================


class TestDateInputsEdgeCases:
    """Stress-test date inputs under various valid, boundary, and malformed strings."""

    @pytest.mark.parametrize(
        "date_str,expected_valid",
        [
            ("2024-02-29", True),  # Leap year (2024)
            ("2000-02-29", True),  # Century leap year (divisible by 400)
            ("2023-02-29", False),  # Non-leap year leap day (invalid)
            (
                "1900-02-29",
                False,
            ),  # Century non-leap year (divisible by 100 but not 400)
            ("0001-01-01", True),  # Minimum valid ISO date boundary
            ("9999-12-31", True),  # Maximum valid ISO date boundary
            ("1970-01-01", True),  # Unix epoch boundary
            ("", False),  # Empty date string
            ("not-a-date", False),  # Arbitrary string
            ("2024-13-45", False),  # Out-of-bounds month/day
            ("05/15/1995", False),  # Non-ISO US format
            ("15-05-1995", False),  # Non-ISO European format
        ],
    )
    def test_type_text_date_inputs(self, date_str: str, expected_valid: bool):
        date_el = FakeElement("input", type="date", name="birth_date")
        controller = controller_with(FakePage([date_el]))
        controller.list_elements()

        if not expected_valid and date_str:
            with pytest.raises(RuntimeError, match="verified"):
                controller.type_text(1, date_str)
            assert date_el.input_value() == ""
            return
        result = controller.type_text(1, date_str)
        assert "Typed into the textbox" in result
        assert date_el.input_value() == date_str

    def test_validate_value_field_accepts_date_and_time_variants(self):
        for field_type in ("date", "datetime-local", "month", "time"):
            el = FakeElement("input", type=field_type)
            # Should not raise
            BrowserController._validate_value_field(el, writable=True)
            BrowserController._validate_value_field(el, writable=False)

    def test_validate_value_field_rejects_unsupported_input_types(self):
        for field_type in (
            "password",
            "file",
            "hidden",
            "color",
            "range",
            "submit",
            "button",
        ):
            el = FakeElement("input", type=field_type)
            with pytest.raises(ValueError) as exc:
                BrowserController._validate_value_field(el, writable=True)
            assert (
                "Only ordinary text, date, and select fields can be read or typed into"
                in str(exc.value)
            )

    def test_validate_value_field_rejects_readonly_date_input(self):
        el = FakeElement("input", type="date", readonly="readonly")
        with pytest.raises(ValueError) as exc:
            BrowserController._validate_value_field(el, writable=True)
        assert "Cannot type into a read-only field." in str(exc.value)


# ===========================================================================
# 2. Datetime-local and Month Inputs
# ===========================================================================


class TestDatetimeAndMonthInputs:
    """Stress-test datetime-local and month inputs."""

    @pytest.mark.parametrize(
        "dt_str",
        [
            "2024-06-15T14:30",
            "2024-06-15T14:30:00",
            "2024-12-31T23:59:59.999",
            "1900-01-01T00:00",
            "9999-12-31T23:59",
            "invalid-dt",
        ],
    )
    def test_type_text_datetime_local(self, dt_str: str):
        dt_el = FakeElement("input", type="datetime-local", name="appointment")
        controller = controller_with(FakePage([dt_el]))
        controller.list_elements()

        if dt_str == "invalid-dt":
            with pytest.raises(RuntimeError, match="verified"):
                controller.type_text(1, dt_str)
            return
        result = controller.type_text(1, dt_str)
        assert "Typed into the textbox" in result
        assert dt_el.input_value() == (
            dt_str.removesuffix(":00") if dt_str.endswith(":00") else dt_str
        )

    @pytest.mark.parametrize(
        "month_str",
        [
            "2024-01",
            "2024-12",
            "1970-01",
            "9999-12",
            "2024-13",  # invalid month
            "invalid-month",
        ],
    )
    def test_type_text_month(self, month_str: str):
        month_el = FakeElement("input", type="month", name="grad_month")
        controller = controller_with(FakePage([month_el]))
        controller.list_elements()

        if month_str in {"2024-13", "invalid-month"}:
            with pytest.raises(RuntimeError, match="verified"):
                controller.type_text(1, month_str)
            return
        result = controller.type_text(1, month_str)
        assert "Typed into the textbox" in result
        assert month_el.input_value() == month_str


# ===========================================================================
# 3. Select Dropdowns: Edge Cases (Duplicates, Special Chars, Whitespace, Empty, Case)
# ===========================================================================


class TestSelectDropdownEdgeCases:
    """Stress-test select dropdown options under complex and adversarial conditions."""

    def test_duplicate_labels_picks_first_or_distinguishes_by_value(self):
        """Dropdown with identical visible text but different values."""
        options = [
            FakeOption("Engineer", value="eng_frontend"),
            FakeOption("Engineer", value="eng_backend"),
            FakeOption("Manager", value="mgr"),
        ]
        sel_el = FakeSelectElement(options=options)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        with pytest.raises(ValueError, match="ambiguous"):
            controller.select_dropdown(1, "Engineer")
        assert sel_el.actions == []

        # Selecting specifically by unique value targets the second option
        controller.list_elements()
        result2 = controller.select_dropdown(1, "eng_backend")
        assert "Selected 'eng_backend'" in result2
        assert sel_el.selected_value == "eng_backend"

    def test_duplicate_values_distinguishes_by_text(self):
        """Dropdown with distinct visible text sharing the same value."""
        options = [
            FakeOption("United States", value="US"),
            FakeOption("USA", value="US"),
            FakeOption("United Kingdom", value="UK"),
        ]
        sel_el = FakeSelectElement(options=options)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        result = controller.select_dropdown(1, "USA")
        assert "Selected 'USA'" in result
        assert sel_el.selected_value in ("US", "USA")

    @pytest.mark.parametrize(
        "label,value",
        [
            ("C++ / C# (.NET)", "cpp_csharp"),
            ("Rock & Roll <Live>", "rock_and_roll"),
            ("100% Remote (US/EU)", "remote_100"),
            ('Quote "Testing"', "quotes"),
            ("Special @#$%! Characters", "symbols"),
            ("Café & München", "cafe_munchen"),
            ("🚀 Space Exploration", "space"),
        ],
    )
    def test_special_characters_in_options(self, label: str, value: str):
        options = [
            FakeOption("Default", value="def"),
            FakeOption(label, value=value),
        ]
        sel_el = FakeSelectElement(options=options)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        # Match by special label
        res_label = controller.select_dropdown(1, label)
        assert f"Selected '{label}'" in res_label

        # Match by special value
        controller.list_elements()
        res_val = controller.select_dropdown(1, value)
        assert f"Selected '{value}'" in res_val

    def test_whitespace_handling_in_option_and_search_query(self):
        options = [
            FakeOption("  Leading and Trailing  ", value="spaces"),
            FakeOption("Multiple    Spaces", value="multi_space"),
        ]
        sel_el = FakeSelectElement(options=options)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        # Search query with extra whitespace
        result = controller.select_dropdown(1, "  Leading and Trailing  ")
        assert "Selected" in result

        # Search query with clean text for padded option
        controller.list_elements()
        result2 = controller.select_dropdown(1, "Leading and Trailing")
        assert "Selected" in result2

    def test_empty_option_selection(self):
        options = [
            FakeOption("-- Please Select --", value=""),
            FakeOption("Option 1", value="opt1"),
        ]
        sel_el = FakeSelectElement(options=options)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        # Selecting empty value
        result = controller.select_dropdown(1, "")
        assert "Selected ''" in result

    def test_dropdown_with_no_options_raises_helpful_error(self):
        sel_el = StressFakeSelectElement(options=[])
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        with pytest.raises(ValueError) as exc:
            controller.select_dropdown(1, "any_option")
        assert "Available options: none" in str(exc.value)

    @pytest.mark.parametrize(
        "query,expected_match",
        [
            ("software engineer", "Software Engineer"),
            ("SOFTWARE ENGINEER", "Software Engineer"),
            ("SoFtWaRe EnGiNeEr", "Software Engineer"),
            ("SWE", "swe"),  # case-insensitive match on value
            ("swe", "swe"),
        ],
    )
    def test_case_insensitive_matching(self, query: str, expected_match: str):
        options = [
            FakeOption("Select Role", value=""),
            FakeOption("Software Engineer", value="swe"),
            FakeOption("Product Manager", value="pm"),
        ]
        sel_el = FakeSelectElement(options=options)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        result = controller.select_dropdown(1, query)
        assert f"Selected '{query}'" in result


# ===========================================================================
# 4. Attempting select_dropdown on Non-Select Elements
# ===========================================================================


class TestSelectDropdownNonSelectElements:
    """Verify that calling select_dropdown on non-select elements rejects with clean errors."""

    @pytest.mark.parametrize(
        "tag,role,attrs",
        [
            ("div", "combobox", {"role": "combobox"}),
            ("button", "button", {"type": "button", "text": "Submit Form"}),
            ("input", "textbox", {"type": "text", "name": "username"}),
            ("a", "link", {"href": "https://example.com", "text": "Click Here"}),
            ("span", "text", {"text": "Header Text"}),
            ("textarea", "textbox", {"name": "bio"}),
        ],
    )
    def test_select_dropdown_rejects_non_select(self, tag: str, role: str, attrs: dict):
        text = attrs.pop("text", "")
        el = FakeElement(tag, text=text, **attrs)
        controller = controller_with(FakePage([el]))
        controller.list_elements()

        with pytest.raises(ValueError) as exc:
            controller.select_dropdown(1, "some_option")
        err_msg = str(exc.value)
        assert "not a select dropdown" in err_msg


# ===========================================================================
# 5. Attempting type_text on a <select> Element
# ===========================================================================


class TestTypeTextOnSelectElement:
    """Verify that calling type_text on a <select> element rejects and guides to select_dropdown."""

    def test_type_text_on_select_rejects_and_guides(self):
        sel_el = FakeSelectElement(name="country")
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        with pytest.raises(ValueError) as exc:
            controller.type_text(1, "Canada")
        err_msg = str(exc.value)
        assert "cannot be typed" in err_msg
        assert "use select_dropdown" in err_msg

    def test_validate_value_field_writable_rejects_select(self):
        sel_el = FakeSelectElement(name="country")
        with pytest.raises(ValueError) as exc:
            BrowserController._validate_value_field(sel_el, writable=True)
        err_msg = str(exc.value)
        assert (
            "Select dropdown elements cannot be typed into; use select_dropdown instead."
            in err_msg
        )

    def test_validate_value_field_readable_accepts_select(self):
        sel_el = FakeSelectElement(name="country")
        # Should not raise when writable=False (used by observe("page_value"))
        BrowserController._validate_value_field(sel_el, writable=False)


# ===========================================================================
# 6. Disabled Options and Disabled Select Elements
# ===========================================================================


class TestDisabledSelectAndOptions:
    """Stress-test disabled selects and disabled options within enabled selects."""

    def test_disabled_select_via_dom_attribute_rejected(self):
        sel_el = StressFakeSelectElement(disabled=True)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        with pytest.raises(ValueError) as exc:
            controller.select_dropdown(1, "Engineering")
        assert "Cannot select an option from a disabled dropdown" in str(exc.value)

    def test_disabled_select_via_is_enabled_false_rejected(self):
        sel_el = FakeSelectElement()
        sel_el._attrs["disabled"] = ""  # HTML5 boolean attribute: <select disabled>
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        with pytest.raises(ValueError) as exc:
            controller.select_dropdown(1, "Engineering")
        assert "Cannot select an option from a disabled dropdown" in str(exc.value)

    def test_disabled_option_within_enabled_select(self):
        """CRITICAL CHALLENGE: Dropdown has enabled and disabled options."""
        options = [
            StressFakeOption("Active Option 1", value="act1", disabled=False),
            StressFakeOption("Disabled Option", value="dis1", disabled=True),
            StressFakeOption("Active Option 2", value="act2", disabled=False),
        ]
        sel_el = StressFakeSelectElement(options=options, disabled=False)
        controller = controller_with(FakePage([sel_el]))
        controller.list_elements()

        # Selecting active option succeeds
        res = controller.select_dropdown(1, "Active Option 1")
        assert "Selected 'Active Option 1'" in res

        controller.list_elements()
        with pytest.raises(ValueError, match="disabled option"):
            controller.select_dropdown(1, "Disabled Option")
        assert sel_el.selected_value == "act1"
