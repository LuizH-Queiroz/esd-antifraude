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
from fastapi.responses import JSONResponse

from gateway.proxy import forward_json
from gateway.schemas import TransactionEvent

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.post("/events/transactions")
async def receive_transaction_event(event: TransactionEvent, request: Request) -> JSONResponse:
    """Recebe um evento de transação e o encaminha ao Ingestion Service.

    IMPORTANTE: o status code da resposta é o **mesmo** devolvido pelo
    Ingestion Service (não um valor fixo). Isso é essencial porque o
    Ingestion Service pode aceitar um evento (persistindo-o) e ainda assim
    falhar ao publicá-lo no RabbitMQ — nesse caso ele responde 503, e o
    Gateway precisa repassar esse 503 para quem chamou, e não mascará-lo
    como um 202 de sucesso. O simulador já trata 503 como retryable (ver
    simulator/app/client.py), então essa propagação é o que faz o retry
    automático funcionar de ponta a ponta.

    Respostas possíveis (refletindo o que o Ingestion Service devolver):
      - 202 Accepted: evento persistido e publicado com sucesso.
      - 422 Unprocessable Entity: gerado pelo FastAPI/Pydantic se o corpo
        da requisição não corresponder ao envelope esperado (validado
        aqui no próprio Gateway, antes de qualquer chamada de rede).
      - 503 Service Unavailable: o Ingestion Service não respondeu (não
        existe/está fora do ar), OU respondeu mas falhou ao publicar no
        broker depois de já ter persistido o evento.
    """
    settings = request.app.state.settings
    http_client = request.app.state.http_client

    # O header Idempotency-Key (já enviado pelo simulador — ver
    # simulator/app/client.py) é repassado sem alterações. Quem decide o que
    # fazer com uma repetição é o Ingestion Service — o Gateway é só um
    # roteador, não deve guardar estado de negócio.
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

    return JSONResponse(
        status_code=downstream_response.status_code,
        content={
            "status": "accepted" if downstream_response.status_code < 300 else "rejected",
            "event_id": event.event_id,
            "routed_to": "ingestion-service",
            "downstream_status_code": downstream_response.status_code,
        },
    )