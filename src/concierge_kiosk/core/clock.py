"""Property-local calendar helpers for effective-dated hotel data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


def property_today(timezone_name: str) -> str:
    """Return the property's current ISO date, independent of server timezone."""
    return datetime.now(ZoneInfo(timezone_name)).date().isoformat()


@dataclass(frozen=True)
class TurnContext:
    """Immutable clock snapshot shared by every stage of one guest turn."""
    property_date: str

    @classmethod
    def capture(cls, timezone_name: str) -> "TurnContext":
        return cls(property_date=property_today(timezone_name))
