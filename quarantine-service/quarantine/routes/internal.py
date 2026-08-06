"""Endpoints internos usados para testes, integração local e validação.

Esses endpoints mantêm o mesmo fluxo de negócio do consumidor RabbitMQ, mas
fornecem uma interface HTTP simples para testes automatizados e validação
manual sem depender de um broker ativo.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from quarantine.processor import process_release_command

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/quarantine", status_code=202)
async def quarantine(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    repository = request.app.state.repository
    broker = request.app.state.broker
    account_id = str(payload.get("account_id"))
    repository.set_quarantined(account_id, payload.get("risk_score"), payload.get("motivo"))
    await broker.publish(
        {
            "event_id": payload.get("event_id", f"event-{account_id}"),
            "event_type": "ContaEmQuarentena",
            "occurred_at": payload.get("occurred_at"),
            "account_id": account_id,
            "risk_score": payload.get("risk_score"),
            "motivo": payload.get("motivo"),
        }
    )
    return {"status": "accepted", "account_id": account_id}


@router.post("/release", status_code=202)
async def release(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    repository = request.app.state.repository
    broker = request.app.state.broker
    account_id = str(payload.get("account_id"))
    repository.set_released(account_id, payload.get("motivo"))
    await broker.publish(
        {
            "event_id": f"release-{account_id}",
            "event_type": "ContaLiberada",
            "occurred_at": payload.get("occurred_at"),
            "account_id": account_id,
            "released_by": "quarantine-service",
        }
    )
    return {"status": "accepted", "account_id": account_id}


@router.post("/commands", status_code=202)
async def commands(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    repository = request.app.state.repository
    broker = request.app.state.broker
    processed = await process_release_command(payload, repository, broker)
    return {
        "status": "accepted" if processed else "ignored",
        "account_id": str(payload.get("account_id") or ""),
    }
