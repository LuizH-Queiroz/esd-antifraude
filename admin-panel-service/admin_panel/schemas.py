"""Schemas HTTP e dos eventos recebidos do Quarantine Service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QuarantineBrokerEvent(BaseModel):
    """Contrato canônico dos eventos consumidos do RabbitMQ.

    O contrato preferido usa os campos no nível superior. Para facilitar a
    integração com o Quarantine Service ainda não implementado, também é
    aceito um objeto ``payload`` contendo os campos de domínio.
    """

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(..., min_length=1)
    event_type: Literal["ContaEmQuarentena", "ContaLiberada"]
    occurred_at: datetime
    account_id: str = Field(..., min_length=1)
    risk_score: float | None = Field(default=None, ge=0, le=1)
    motivo: str | None = None
    released_by: str | None = None

    @model_validator(mode="before")
    @classmethod
    def flatten_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        nested = value.get("payload")
        if not isinstance(nested, dict):
            return value
        merged = {**nested, **value}
        merged.pop("payload", None)
        return merged

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @model_validator(mode="after")
    def validate_quarantine_fields(self) -> QuarantineBrokerEvent:
        if self.event_type == "ContaEmQuarentena" and self.risk_score is None:
            raise ValueError("ContaEmQuarentena precisa informar risk_score.")
        return self


class ReleaseRequest(BaseModel):
    """Dados opcionais informados pelo administrador ao solicitar liberação."""

    requested_by: str = Field(default="admin", min_length=1, max_length=120)
    motivo: str | None = Field(default=None, max_length=500)


class CaseEventResponse(BaseModel):
    event_id: str
    tipo: str
    occurred_at: datetime
    actor: str | None = None
    motivo: str | None = None


class CaseSummaryResponse(BaseModel):
    account_id: str
    status: Literal["EM_QUARENTENA", "LIBERADA"]
    risk_score: float | None
    quarantined_at: datetime | None
    updated_at: datetime


class CaseDetailResponse(CaseSummaryResponse):
    motivo: str | None
    eventos: list[CaseEventResponse]


class CaseListResponse(BaseModel):
    items: list[CaseSummaryResponse]
    total: int
    page: int
    page_size: int


class ReleaseResponse(BaseModel):
    status: Literal["accepted"]
    account_id: str
    command_id: str
