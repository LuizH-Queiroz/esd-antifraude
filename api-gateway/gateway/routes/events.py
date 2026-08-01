"""Rota de entrada para eventos de transação vindos do Sistema Bancário.

No caminho-base do C4 de Nível 2 (Sistema Bancário -> API Gateway ->
Ingestion Service -> Message Broker), esta rota implementa o primeiro salto:
recebe o evento, faz uma validação estrutural mínima (o "envelope" definido
em gateway/schemas.py) e o repassa ao Ingestion Service.

Hoje, quem produz esses eventos é o Simulador (ver simulator/README.md), mas
a rota não sabe nem precisa saber disso — ela recebe o mesmo formato que o
Sistema Bancário real enviaria.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from gateway.proxy import forward_json
from gateway.schemas import TransactionEvent

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.post("/events/transactions", status_code=202)
async def receive_transaction_event(event: TransactionEvent, request: Request) -> dict:
    """Recebe um evento de transação e o encaminha ao Ingestion Service.

    Respostas possíveis:
      - 202 Accepted: evento validado estruturalmente e repassado ao
        Ingestion Service (o Gateway não espera nem sabe como o Ingestion
        Service processa o evento — ver ADR 004 sobre o sistema ser
        out-of-band).
      - 422 Unprocessable Entity: gerado automaticamente pelo FastAPI/Pydantic
        se o corpo da requisição não corresponder ao envelope esperado.
      - 503 Service Unavailable: o Ingestion Service não respondeu (ainda não
        existe, ou está fora do ar). O simulador já trata este código como
        retryable (ver simulator/app/client.py), então nenhuma mudança será
        necessária nele quando o Ingestion Service passar a existir de fato.
    """
    settings = request.app.state.settings
    http_client = request.app.state.http_client

    # O header Idempotency-Key (já enviado pelo simulador — ver
    # simulator/app/client.py) é repassado sem alterações. Quem decide o que
    # fazer com uma repetição (ex.: descartar um evento já processado) é o
    # Ingestion Service, que terá o Event Store — o Gateway é só um roteador,
    # não deve guardar estado de negócio.
    idempotency_key = request.headers.get("Idempotency-Key")
    forwarded_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None

    downstream_response = await forward_json(
        http_client,
        base_url=settings.ingestion_service_url,
        path=settings.ingestion_events_path,
        json_body=event.to_downstream_payload(),
        forwarded_headers=forwarded_headers,
    )

    LOGGER.info(
        "Evento %s roteado ao Ingestion Service (HTTP %s).",
        event.event_id,
        downstream_response.status_code,
    )

    return {
        "status": "accepted",
        "event_id": event.event_id,
        "routed_to": "ingestion-service",
        "downstream_status_code": downstream_response.status_code,
    }