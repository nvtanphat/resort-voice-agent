"""Catalog row validation and server-shaped service candidates; no semantic ranking."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from concierge_kiosk.domain.service_registry import accepted_slots


@dataclass(frozen=True)
class ServiceCatalogEntry:
    catalog_service_id: str
    name: str
    description: str
    names_by_locale: tuple[tuple[str, str], ...]
    embedding_text: str


@dataclass(frozen=True)
class ServiceCandidate:
    """A server-shaped candidate safe to place in an understanding prompt."""

    service_mode: str
    request_kind: str
    catalog_service_id: str | None
    name: str
    description: str
    score: float

    def public(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "service_mode": self.service_mode,
            "request_kind": self.request_kind,
            "accepted_slots": list(accepted_slots(self.service_mode)),
        }
        if self.catalog_service_id:
            value["catalog_service_id"] = self.catalog_service_id
        if self.name:
            value["name"] = self.name[:180]
        if self.description:
            value["description"] = self.description[:320]
        return value


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:maximum]


def _catalog_entry(row: object) -> ServiceCatalogEntry | None:
    if not isinstance(row, dict):
        return None
    service_id = _text(row.get("service_id"), 128)
    name = _text(row.get("name"), 180)
    description = _text(row.get("description"), 640)
    localized = row.get("names_by_locale")
    if not service_id or not isinstance(localized, dict):
        return None
    names: list[tuple[str, str]] = []
    for language, value in localized.items():
        language_text = _text(language, 16)
        name_text = _text(value, 180)
        if language_text and name_text:
            names.append((language_text, name_text))
    if not names and not name:
        return None
    labels = " | ".join(dict.fromkeys(value for _, value in names))
    embedding_text = ". ".join(
        value for value in (labels, description) if value).strip()
    if not embedding_text:
        return None
    return ServiceCatalogEntry(
        catalog_service_id=service_id,
        name=name or names[0][1],
        description=description,
        names_by_locale=tuple(names),
        embedding_text=embedding_text,
    )
