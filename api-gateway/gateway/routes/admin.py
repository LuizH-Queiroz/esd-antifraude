"""Rota de entrada para consultas e comandos do Administrador.

Segundo papel do Gateway descrito no C4 de Nível 2: rotear requisições REST
do Administrador (consultas a contas em quarentena, comandos de liberação
manual) para o Admin Panel Service.

O Admin Panel Service ainda não existe, e seu contrato de API também não foi
definido — por isso esta rota é um proxy **genérico**: qualquer método HTTP,
qualquer sub-caminho sob `/admin/`, é repassado como está. Quando o contrato
real existir, a expectativa é que rotas mais específicas (com validação via
Pydantic, como em routes/events.py) substituam este catch-all — ele serve, por
ora, para validar que o roteamento básico do Gateway funciona nos dois
sentidos (para o Ingestion Service E para o Admin Panel Service), sem
bloquear o restante do trabalho até o Admin Panel Service ser implementado.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request

from gateway.proxy import forward_request

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

_FORWARDED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/admin/{downstream_path:path}", methods=_FORWARDED_METHODS)
async def route_to_admin_panel(downstream_path: str, request: Request) -> dict:
    """Repassa qualquer requisição sob /admin/** ao Admin Panel Service.

    Ex.: `GET /admin/contas-em-quarentena` -> `GET
    {ADMIN_PANEL_SERVICE_URL}/contas-em-quarentena`.

    Retorna 503 se o Admin Panel Service não responder — o mesmo tratamento
    dado à rota de eventos (ver routes/events.py), pelo mesmo motivo: o
    serviço de destino ainda não foi implementado.
    """
    settings = request.app.state.settings
    http_client = request.app.state.http_client

    body = await request.body()
    json_body = None
    if body:
        # Corpo é opcional (ex.: GET não costuma ter um). Quando presente,
        # o FastAPI só nos dá bytes brutos aqui (não fazemos validação de
        # schema nesta rota genérica), então repassamos como JSON cru.
        json_body = json.loads(body)

    downstream_response = await forward_request(
        http_client,
        base_url=settings.admin_panel_service_url,
        path=downstream_path,
        method=request.method,
        query_params=dict(request.query_params),
        json_body=json_body,
    )

    LOGGER.info(
        "Requisição do Administrador (%s /admin/%s) roteada ao Admin Panel "
        "Service (HTTP %s).",
        request.method,
        downstream_path,
        downstream_response.status_code,
    )

    return {
        "status": "routed",
        "routed_to": "admin-panel-service",
        "downstream_status_code": downstream_response.status_code,
    }