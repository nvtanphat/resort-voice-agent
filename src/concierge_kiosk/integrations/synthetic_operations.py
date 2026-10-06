"""Read-only adapter for the property's synthetic operational fixtures.

The files under ``datasets/synthetic/operations`` are useful for exercising a
resort workflow, but they are not canonical guest-facing facts.  This adapter
keeps that boundary explicit: every successful observation carries provenance
and a ``synthetic`` marker, and the adapter never creates a booking or mutates
the fixture on disk.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

from concierge_kiosk.core.dataset_layout import (
    dataset_path,
    dataset_root,
)
from concierge_kiosk.domain.service_registry import service_definition


@dataclass(frozen=True)
class SyntheticSource:
    path: str
    classification: str
    truth_status: str
    guest_answer_policy: str
    price_semantics: str | None = None
    demo_booking_mode: str | None = None

    def public(self) -> dict[str, str | bool]:
        result: dict[str, str | bool] = {
            "source_id": f"synthetic:{self.path}",
            "classification": self.classification,
            "truth_status": self.truth_status,
            "guest_answer_policy": self.guest_answer_policy,
            "synthetic": True,
        }
        if self.price_semantics:
            result["price_semantics"] = self.price_semantics
        if self.demo_booking_mode:
            result["demo_booking_mode"] = self.demo_booking_mode
        return result


@dataclass(frozen=True)
class AvailabilityObservation:
    status: str
    source: SyntheticSource
    records: tuple[dict[str, Any], ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    reason: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": [dict(item) for item in self.records],
            "alternatives": [dict(item) for item in self.alternatives],
            "reason": self.reason,
            "source": self.source.public(),
            "synthetic": True,
        }


class SyntheticOperations:
    """Load and query validated synthetic availability without side effects."""

    def __init__(self, cfg: object | None = None, *, root: str | Path | None = None):
        configured = root
        if configured is None:
            configured = getattr(cfg, "structured_dataset_dir", None)
        self._root = dataset_root(configured or None)
        self._cache: dict[str, dict[str, Any] | None] = {}

    @staticmethod
    def _clean_text(value: object) -> str:
        text = unicodedata.normalize("NFKD", str(value or "")).casefold()
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(text.split())

    @classmethod
    def _tokens(cls, value: object) -> set[str]:
        text = cls._clean_text(value)
        return {token for token in re.findall(r"[a-z0-9\u4e00-\u9fff\uac00-\ud7af]+", text)
                if len(token) >= 2}

    def _json(self, relative: str) -> dict[str, Any] | None:
        if relative in self._cache:
            return self._cache[relative]
        path = dataset_path(relative, self._root)
        value: dict[str, Any] | None = None
        if path.is_file() and not path.is_symlink():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                value = payload if isinstance(payload, dict) else None
            except (OSError, UnicodeError, json.JSONDecodeError):
                value = None
        self._cache[relative] = value
        return value

    def _source(self, path: str, payload: Mapping[str, Any]) -> SyntheticSource:
        classification = str(payload.get("classification") or "synthetic_operational")
        truth_status = str(payload.get("truth_status") or classification)
        policy = str(payload.get("guest_answer_policy") or "demo_only_staff_confirmation")
        price = payload.get("price_semantics")
        demo = payload.get("demo_booking_mode")
        return SyntheticSource(
            path=path,
            classification=classification,
            truth_status=truth_status,
            guest_answer_policy=policy,
            price_semantics=str(price) if price is not None else None,
            demo_booking_mode=str(demo) if demo is not None else None,
        )

    @classmethod
    def _matching_rows(cls, query: str, rows: Iterable[Mapping[str, Any]],
                       fields: tuple[str, ...]) -> list[dict[str, Any]]:
        query_text = cls._clean_text(query)
        query_tokens = cls._tokens(query)
        scored: list[tuple[int, dict[str, Any]]] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            labels = [str(raw.get(field) or "") for field in fields]
            label_text = " ".join(labels)
            normalized = cls._clean_text(label_text)
            label_tokens = cls._tokens(label_text)
            score = 0
            if normalized and normalized in query_text:
                score += 100
            score += len(query_tokens & label_tokens) * 10
            if score:
                scored.append((score, dict(raw)))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("entity_id", ""))))
        if not scored:
            return []
        # Keep only the best semantic/data-owned matches. A shared token such
        # as "cafe" must not make a strongly matching "Café Indochine" query
        # ambiguous with every other café in the property.
        top_score = scored[0][0]
        return [row for score, row in scored if score == top_score]

    @staticmethod
    def _at_date(rows: Iterable[Mapping[str, Any]], effective_date: str) -> list[dict[str, Any]]:
        return [dict(row) for row in rows
                if not row.get("date") or str(row.get("date")) == effective_date]

    @staticmethod
    def _at_time(rows: Iterable[Mapping[str, Any]], preferred_time: str | None) -> list[dict[str, Any]]:
        if not preferred_time:
            return [dict(row) for row in rows]
        wanted = str(preferred_time).strip()
        return [dict(row) for row in rows if str(row.get("time") or row.get("depart") or "") == wanted]

    def restaurant_availability(self, *, path: str, query: str, effective_date: str,
                                preferred_time: str | None = None,
                                party_size: int | None = None) -> AvailabilityObservation | None:
        payload = self._json(path)
        if payload is None or not isinstance(payload.get("venues"), list):
            return None
        source = self._source(path, payload)
        venues = self._matching_rows(query, payload["venues"], ("name", "entity_id"))
        if not venues:
            return AvailabilityObservation("ambiguous", source, reason="venue_not_identified")
        if len(venues) > 1 and venues[0].get("name") != venues[1].get("name"):
            options = tuple({"entity_id": row.get("entity_id"), "name": row.get("name")} for row in venues[:3])
            return AvailabilityObservation("ambiguous", source, alternatives=options,
                                           reason="multiple_venues")
        venue = venues[0]
        slots = self._at_date(venue.get("slots", ()), effective_date)
        if not slots:
            # A missing effective date is a data-coverage miss, not proof that
            # the property is sold out.  Let the canonical schedule tool own
            # that fallback rather than presenting stale demo data.
            return None
        slots = self._at_time(slots, preferred_time)
        required = party_size if isinstance(party_size, int) and party_size > 0 else 1
        available = [row for row in slots
                     if str(row.get("status")) == "available"
                     and int(row.get("covers_remaining") or 0) >= required]
        alternatives = [row for row in slots if str(row.get("status")) in {"available", "limited"}]
        status = "available" if available else "unavailable"
        records = tuple({
            "entity_id": venue.get("entity_id"), "name": venue.get("name"),
            "date": row.get("date"), "time": row.get("time"),
            "remaining": row.get("covers_remaining"), "status": row.get("status"),
            "requested_party_size": required,
        } for row in (available[:3] if available else alternatives[:3]))
        return AvailabilityObservation(status, source, records=records,
                                       reason=None if available else "no_matching_capacity")

    def spa_availability(self, *, path: str, effective_date: str, preferred_time: str | None = None,
                         party_size: int | None = None) -> AvailabilityObservation | None:
        payload = self._json(path)
        if payload is None or not isinstance(payload.get("slots"), list):
            return None
        source = self._source(path, payload)
        slots = self._at_date(payload["slots"], effective_date)
        if not slots:
            return None
        slots = self._at_time(slots, preferred_time)
        required = party_size if isinstance(party_size, int) and party_size > 0 else 1
        available = [row for row in slots
                     if str(row.get("status")) == "available"
                     and int(row.get("remaining") or 0) >= required]
        candidates = available or [row for row in slots if str(row.get("status")) in {"available", "limited"}]
        records = tuple({
            "date": row.get("date"), "time": row.get("time"),
            "remaining": row.get("remaining"), "duration_min": row.get("duration_min"),
            "status": row.get("status"), "requested_party_size": required,
        } for row in candidates[:3])
        return AvailabilityObservation("available" if available else "unavailable", source,
                                       records=records,
                                       reason=None if available else "no_matching_capacity")

    def commercial_availability(self, *, path: str, category: str, effective_date: str,
                                product_id: str | None = None) -> AvailabilityObservation | None:
        payload = self._json(path)
        if payload is None:
            return None
        source = self._source(path, payload)
        rows = payload.get(category)
        if not isinstance(rows, list):
            return None
        rows = [dict(row) for row in rows if isinstance(row, Mapping)
                and (not row.get("date") or str(row.get("date")) == effective_date)]
        if not rows:
            return None
        if product_id:
            rows = [row for row in rows if str(row.get("product_id")) == product_id]
        available = [row for row in rows if str(row.get("status")) in {"available", "limited"}
                     and int(row.get("remaining", row.get("available_units", row.get("seats_remaining", 0))) or 0) > 0]
        safe_records = tuple({key: row.get(key) for key in (
            "date", "product_id", "route_id", "direction", "depart", "remaining",
            "available_units", "seats_remaining", "status", "synthetic_price_vnd_per_adult",
        ) if key in row} for row in (available[:3] if available else rows[:3]))
        return AvailabilityObservation("available" if available else "unavailable", source,
                                       records=safe_records,
                                       reason=None if available else "no_matching_capacity")

    def check_availability(self, *, service_code: str, query: str, effective_date: str,
                           preferred_time: str | None = None,
                           party_size: int | None = None) -> AvailabilityObservation | None:
        """Return a read-only synthetic availability observation for a service."""
        definition = service_definition(service_code)
        source = definition.availability_source if definition is not None else None
        if not isinstance(source, Mapping):
            return None
        path = str(source.get("dataset") or "")
        shape = str(source.get("shape") or "")
        collection = str(source.get("collection") or "")
        if not path or not shape:
            return None
        if shape == "table_capacity":
            return self.restaurant_availability(query=query, effective_date=effective_date,
                                                path=path,
                                                preferred_time=preferred_time, party_size=party_size)
        if shape == "time_slots":
            return self.spa_availability(path=path, effective_date=effective_date,
                                         preferred_time=preferred_time, party_size=party_size)
        if shape == "inventory" and collection:
            return self.commercial_availability(path=path, category=collection,
                                                effective_date=effective_date)
        return None


__all__ = ["AvailabilityObservation", "SyntheticOperations", "SyntheticSource"]
