"""Rota que recebe eventos de transação já roteados pelo API Gateway.

Implementa o fluxo decidido para esta Issue: persistir primeiro, publicar
depois, respondendo 202 somente se as duas etapas tiverem sucesso.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ingestion.domain import TransacaoRegistrada
from ingestion.schemas import TransactionEvent

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["transactions"])


@router.post("/internal/transactions")
async def receive_transaction(event: TransactionEvent, request: Request) -> JSONResponse:
    """Persiste o evento no Event Store e o publica no RabbitMQ.

    Respostas possíveis:
      - 202 Accepted: persistido e publicado com sucesso (ou já processado
        antes — ver nota sobre idempotência abaixo).
      - 422 Unprocessable Entity: envelope inválido (gerado automaticamente
        pelo FastAPI/Pydantic).
      - 503 Service Unavailable: o evento foi persistido, mas a publicação
        no RabbitMQ falhou. O Gateway repassa esse 503 para quem o chamou,
        e o simulador trata isso como retryable.
    """
    event_store = request.app.state.event_store
    event_publisher = request.app.state.event_publisher

    # O header Idempotency-Key (enviado pelo simulador via API Gateway) é
    # só informativo aqui — a deduplicação de fato usa o event_id do
    # próprio corpo do evento como chave única no Event Store (decisão
    # tomada na Issue). Registramos o header apenas para facilitar
    # depuração, caso um dia precisemos correlacionar os dois.
    idempotency_key = request.headers.get("Idempotency-Key")

    domain_event = TransacaoRegistrada.from_incoming_event(event)

    inserted = await event_store.append(domain_event)
    if not inserted:
        LOGGER.info(
            "Evento %s já havia sido persistido antes (idempotency_key=%s); "
            "publicando novamente por segurança.",
            domain_event.event_id,
            idempotency_key,
        )
        # Mesmo em uma repetição, tentamos publicar de novo: não temos como
        # saber, só pelo Event Store, se a publicação anterior teve sucesso
        # (ver a limitação de dual-write documentada em publisher.py). Isso
        # aceita a possibilidade de o Risk Scoring Service (futuro) receber
        # a mesma mensagem mais de uma vez — semântica "at-least-once", que
        # ele precisará tratar usando o event_id, e não "exactly-once".

    try:
        await event_publisher.publish("transacao.registrada", domain_event.to_message())
    except Exception:
        LOGGER.exception(
            "Evento %s foi persistido, mas falhou ao publicar no RabbitMQ.",
            domain_event.event_id,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Evento persistido com sucesso, mas houve falha ao publicar "
                "no message broker. Tente novamente."
            ),
        ) from None

    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "event_id": domain_event.event_id},
    )