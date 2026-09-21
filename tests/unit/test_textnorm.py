from __future__ import annotations

from verdict_hot import textnorm


def test_normalize_removes_zero_width_and_bidi_characters_and_counts_them() -> None:
    text = "safe​text‪hidden‬﻿"
    cleaned, removed = textnorm.normalize(text)
    assert cleaned == "safetexthidden"
    assert removed == 4


def test_normalize_removes_ansi_color_codes() -> None:
    text = "\x1b[31mred\x1b[0m plain"
    cleaned, removed = textnorm.normalize(text)
    assert cleaned == "red plain"
    assert removed == len("\x1b[31m") + len("\x1b[0m")


def test_normalize_removes_ansi_osc_sequence() -> None:
    text = "before\x1b]0;window title\x07after"
    cleaned, removed = textnorm.normalize(text)
    assert cleaned == "beforeafter"
    assert removed == len("\x1b]0;window title\x07")


def test_normalize_folds_fullwidth_letters_via_nfkc() -> None:
    # Fullwidth Latin "ABC" (U+FF21-FF23) NFKC-folds to ASCII "ABC".
    cleaned, removed = textnorm.normalize("ＡＢＣ")
    assert cleaned == "ABC"
    assert removed == 0


def test_normalize_handles_plain_ascii_untouched() -> None:
    cleaned, removed = textnorm.normalize("nothing special here")
    assert cleaned == "nothing special here"
    assert removed == 0


def test_truncate_leaves_short_text_untouched() -> None:
    text = "short text"
    assert textnorm.truncate_anchored(text, head=100, tail=100) == text


def test_truncate_keeps_exact_head_and_tail_and_reports_count() -> None:
    text = "H" * 50 + "M" * 900 + "T" * 50
    result = textnorm.truncate_anchored(text, head=50, tail=50)
    assert result.startswith("H" * 50)
    assert result.endswith("T" * 50)
    assert "[900 characters truncated]" in result


def test_truncate_never_drops_the_tail_even_with_tiny_head() -> None:
    text = "x" * 1000
    result = textnorm.truncate_anchored(text, head=0, tail=10)
    assert result.endswith("x" * 10)


def test_error_line_in_last_100_chars_survives_500kb_of_padding() -> None:
    padding = "filler " * ((500 * 1024) // len("filler "))
    error_line = "FATAL: build failed at step 42"
    text = padding + error_line
    result = textnorm.truncate_anchored(text, head=4000, tail=4000)
    assert error_line in result
    assert result.startswith(padding[:4000])
