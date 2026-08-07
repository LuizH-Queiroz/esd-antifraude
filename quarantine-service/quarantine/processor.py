"""Regras de negócio para processamento de comandos de liberação.

Este módulo centraliza a transição de estado da conta quando o Quarantine
Service recebe um comando de liberação. A ideia é manter o mesmo fluxo tanto
para os endpoints internos usados em testes quanto para o consumidor RabbitMQ
real, evitando divergência entre a execução local e a integração assíncrona.

O fluxo cobre duas decisões importantes do serviço: a transição de
``EM_QUARENTENA`` para ``LIBERADA`` e a publicação do evento ``ContaLiberada``
a partir do mesmo comando recebido.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime
from typing import Any

LOGGER = logging.getLogger(__name__)


async def _maybe_await(value: Any) -> Any:
    """Executa await quando o objeto fornecido for uma coroutine."""

    if inspect.isawaitable(value):
        return await value
    return value


async def process_release_command(
    payload: dict[str, Any], repository: Any, broker: Any
) -> bool:
    """Processa um comando de liberação e publica o evento de saída.

    O comando é tratado de forma idempotente: se o mesmo ``event_id`` já foi
    processado anteriormente, a operação é ignorada sem publicar um novo
    ``ContaLiberada``.
    """

    account_id = str(payload.get("account_id") or "").strip()
    event_id = str(payload.get("event_id") or "").strip()
    if not account_id:
        raise ValueError("account_id is required")
    if not event_id:
        raise ValueError("event_id is required")

    processed = await _maybe_await(repository.process_release_command(payload))
    if not processed:
        return False

    release_payload = {
        "event_id": f"{event_id}-release",
        "event_type": "ContaLiberada",
        "occurred_at": payload.get("occurred_at") or datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "released_by": "quarantine-service",
        "motivo": payload.get("motivo"),
    }

    if broker is not None:
        await _maybe_await(broker.publish(release_payload))

    return True


async def process_quarantine_trigger(
    payload: dict[str, Any], repository: Any, broker: Any
) -> bool:
    """Processa um evento ScoreAltoRisco, aplicando a quarentena da conta.

    Espelha a lógica do endpoint POST /internal/quarantine (ver
    routes/internal.py) — os dois caminhos (HTTP para testes/integração
    local, e este, para o consumo real via RabbitMQ) convergem para a
    mesma regra de negócio.
    """
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    risk_score = payload.get("risk_score")
    motivo = payload.get("motivo")

    await _maybe_await(repository.set_quarantined(account_id, risk_score, motivo))

    quarantine_payload = {
        "event_id": payload.get("event_id", f"quarantine-{account_id}"),
        "event_type": "ContaEmQuarentena",
        "occurred_at": payload.get("occurred_at") or datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "risk_score": risk_score,
        "motivo": motivo,
    }

    if broker is not None:
        await _maybe_await(broker.publish(quarantine_payload))

    return True