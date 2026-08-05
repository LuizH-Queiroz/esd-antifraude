"""Rotas de consulta de casos e solicitação de liberação manual."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request

from admin_panel.domain import CaseRecord, CaseStatus, ReleaseCommand
from admin_panel.schemas import (
    CaseDetailResponse,
    CaseEventResponse,
    CaseListResponse,
    CaseSummaryResponse,
    ReleaseRequest,
    ReleaseResponse,
)

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["cases"])


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _summary(case: CaseRecord) -> CaseSummaryResponse:
    return CaseSummaryResponse(
        account_id=case.account_id,
        status=case.status.value,
        risk_score=case.risk_score,
        quarantined_at=case.quarantined_at,
        updated_at=case.updated_at,
    )


@router.get("/cases", response_model=CaseListResponse)
async def list_cases(
    request: Request,
    status: CaseStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_score: float | None = Query(default=None, ge=0, le=1),
    max_score: float | None = Query(default=None, ge=0, le=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> CaseListResponse:
    date_from = _ensure_aware(date_from)
    date_to = _ensure_aware(date_to)

    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from não pode ser maior que date_to.")
    if min_score is not None and max_score is not None and min_score > max_score:
        raise HTTPException(status_code=422, detail="min_score não pode ser maior que max_score.")

    result = await request.app.state.case_repository.list_cases(
        status=status,
        date_from=date_from,
        date_to=date_to,
        min_score=min_score,
        max_score=max_score,
        page=page,
        page_size=page_size,
    )
    return CaseListResponse(
        items=[_summary(case) for case in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/cases/{account_id}", response_model=CaseDetailResponse)
async def get_case(account_id: str, request: Request) -> CaseDetailResponse:
    case = await request.app.state.case_repository.get_case(account_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso não encontrado.")

    return CaseDetailResponse(
        **_summary(case).model_dump(),
        motivo=case.motivo,
        eventos=[
            CaseEventResponse(
                event_id=event.event_id,
                tipo=event.event_type,
                occurred_at=event.occurred_at,
                actor=event.actor,
                motivo=event.motivo,
            )
            for event in case.events
        ],
    )


@router.post(
    "/cases/{account_id}/release",
    response_model=ReleaseResponse,
    status_code=202,
)
async def release_case(
    account_id: str,
    request: Request,
    payload: ReleaseRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ReleaseResponse:
    repository = request.app.state.case_repository
    broker = request.app.state.message_broker

    case = await repository.get_case(account_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso não encontrado.")
    if case.status != CaseStatus.EM_QUARENTENA:
        raise HTTPException(status_code=409, detail="A conta não está em quarentena.")

    release_data = payload or ReleaseRequest()
    command = ReleaseCommand(
        command_id=idempotency_key or str(uuid4()),
        account_id=account_id,
        occurred_at=datetime.now(UTC),
        requested_by=release_data.requested_by,
        motivo=release_data.motivo,
    )

    # O registro append-only é feito antes da publicação, preservando a
    # auditoria da decisão humana mesmo se o broker falhar depois.
    await repository.append_release_command(command)
    try:
        await broker.publish_release_command(command)
    except Exception:
        LOGGER.exception("Falha ao publicar ComandoDeLiberacao %s.", command.command_id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Comando registrado para auditoria, mas houve falha ao publicar "
                "no RabbitMQ. Repita com o mesmo Idempotency-Key."
            ),
        ) from None

    return ReleaseResponse(
        status="accepted",
        account_id=account_id,
        command_id=command.command_id,
    )
