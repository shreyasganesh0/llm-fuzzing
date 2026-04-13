"""Format-similarity labeling (plan §Phase Transfer T.5)."""
from __future__ import annotations

from core.config import format_pair


def test_text_text_pairs():
    assert format_pair("re2", "libxml2") == "text_text"
    assert format_pair("sqlite3", "proj") == "text_text"


def test_binary_binary_pairs():
    assert format_pair("libpng", "lcms") == "binary_binary"
    assert format_pair("harfbuzz", "freetype") == "binary_binary"


def test_text_binary_pairs():
    assert format_pair("re2", "libpng") == "text_binary"
    assert format_pair("libjpeg-turbo", "sqlite3") == "text_binary"
