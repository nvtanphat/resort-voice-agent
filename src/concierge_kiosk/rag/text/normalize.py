"""Surface-form normalization shared by indexing, retrieval and claim checks."""
from __future__ import annotations
import unicodedata
from concierge_kiosk.core.terminology import normalize_terminology


def fold_accents(value: str) -> str:
    """Fold Latin diacritics for *fallback* matching without touching CJK/Hangul.

    Vietnamese diacritics carry lexical meaning (for example ``bàn`` vs ``bán``),
    so the primary retrieval channel must keep them.  This helper is only used
    when we intentionally need a no-diacritic fallback representation.
    """
    value = unicodedata.normalize("NFKC", value).casefold().replace("đ", "d")
    return "".join(
        c if ("\uac00" <= c <= "\ud7a3" or "\u4e00" <= c <= "\u9fff")
        else "".join(d for d in unicodedata.normalize("NFKD", c)
                     if not unicodedata.combining(d))
        for c in value
    )


def searchable(value: str, language: str | None = None) -> str:
    """Return the normalized surface form used by policy and phrase checks."""
    return normalize_terminology(unicodedata.normalize("NFKC", value).casefold(), language)
