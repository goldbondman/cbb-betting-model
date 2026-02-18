"""Tests for core.db_utils data-sanitization helpers."""

import math

from core.db_utils import has_text, resolve_first, safe_float, sanitize_nan_dict


class TestSafeFloat:
    def test_none(self) -> None:
        assert safe_float(None) is None

    def test_nan(self) -> None:
        assert safe_float(float("nan")) is None

    def test_empty_string(self) -> None:
        assert safe_float("") is None

    def test_whitespace(self) -> None:
        assert safe_float("  ") is None

    def test_valid_int_string(self) -> None:
        assert safe_float("42") == 42.0

    def test_valid_float_string(self) -> None:
        assert safe_float("3.14") == 3.14

    def test_numeric_input(self) -> None:
        assert safe_float(7) == 7.0

    def test_invalid_string(self) -> None:
        assert safe_float("abc") is None

    def test_negative(self) -> None:
        assert safe_float("-5.5") == -5.5


class TestHasText:
    def test_none(self) -> None:
        assert has_text(None) is False

    def test_nan(self) -> None:
        assert has_text(float("nan")) is False

    def test_empty(self) -> None:
        assert has_text("") is False

    def test_whitespace(self) -> None:
        assert has_text("   ") is False

    def test_valid(self) -> None:
        assert has_text("hello") is True

    def test_numeric(self) -> None:
        assert has_text(123) is True


class TestSanitizeNanDict:
    def test_replaces_nan_with_none(self) -> None:
        result = sanitize_nan_dict({"a": 1, "b": float("nan"), "c": "x"})
        assert result == {"a": 1, "b": None, "c": "x"}

    def test_preserves_none(self) -> None:
        result = sanitize_nan_dict({"a": None})
        assert result == {"a": None}

    def test_empty_dict(self) -> None:
        assert sanitize_nan_dict({}) == {}

    def test_no_mutation(self) -> None:
        original = {"x": float("nan")}
        sanitize_nan_dict(original)
        assert math.isnan(original["x"])


class TestResolveFirst:
    def test_returns_first_match(self) -> None:
        row = {"a": None, "b": "5.0", "c": "10.0"}
        assert resolve_first(row, ["a", "b", "c"]) == 5.0

    def test_skips_none(self) -> None:
        row = {"a": None, "b": "7"}
        assert resolve_first(row, ["a", "b"]) == 7.0

    def test_returns_none_when_all_missing(self) -> None:
        assert resolve_first({}, ["x", "y"]) is None

    def test_skips_nan(self) -> None:
        row = {"a": float("nan"), "b": "3"}
        assert resolve_first(row, ["a", "b"]) == 3.0
