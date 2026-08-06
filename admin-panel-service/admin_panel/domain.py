"""Objetos de domínio do Admin Panel Service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from admin_panel.schemas import QuarantineBrokerEvent


class CaseStatus(StrEnum):
    EM_QUARENTENA = "EM_QUARENTENA"
    LIBERADA = "LIBERADA"


@dataclass(frozen=True, slots=True)
class QuarantineEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    account_id: str
    risk_score: float | None
    motivo: str | None
    released_by: str | None
    payload: dict[str, Any]

    @classmethod
    def from_broker_event(cls, event: QuarantineBrokerEvent) -> QuarantineEvent:
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            account_id=event.account_id,
            risk_score=event.risk_score,
            motivo=event.motivo,
            released_by=event.released_by,
            payload=event.model_dump(mode="json"),
        )


@dataclass(frozen=True, slots=True)
class ReleaseCommand:
    command_id: str
    account_id: str
    occurred_at: datetime
    requested_by: str
    motivo: str | None

    def to_message(self) -> dict[str, Any]:
        return {
            "event_id": self.command_id,
            "event_type": "ComandoDeLiberacao",
            "occurred_at": self.occurred_at.isoformat(),
            "account_id": self.account_id,
            "requested_by": self.requested_by,
            "motivo": self.motivo,
        }


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    actor: str | None = None
    motivo: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CaseRecord:
    account_id: str
    status: CaseStatus
    risk_score: float | None
    motivo: str | None
    quarantined_at: datetime | None
    updated_at: datetime
    events: list[AuditEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CasePage:
    items: list[CaseRecord]
    total: int
    page: int
    page_size: int
