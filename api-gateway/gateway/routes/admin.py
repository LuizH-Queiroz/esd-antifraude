"""Proxy das requisições administrativas para o Admin Panel Service."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request, Response

from gateway.proxy import forward_request

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])
_FORWARDED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/admin/{downstream_path:path}", methods=_FORWARDED_METHODS)
async def route_to_admin_panel(downstream_path: str, request: Request) -> Response:
    """Encaminha `/admin/**` removendo o prefixo e devolve a resposta real.

    Exemplo: `GET /admin/cases` vira `GET {ADMIN_PANEL_SERVICE_URL}/cases`.
    Diferentemente da versão placeholder, o corpo e o status produzidos pelo
    Admin Panel Service são preservados para o cliente externo.
    """

    settings = request.app.state.settings
    http_client = request.app.state.http_client
    body = await request.body()
    json_body = json.loads(body) if body else None

    downstream_response = await forward_request(
        http_client,
        base_url=settings.admin_panel_service_url,
        path=downstream_path,
        method=request.method,
        query_params=dict(request.query_params),
        json_body=json_body,
    )

    LOGGER.info(
        "Requisição administrativa %s /admin/%s -> HTTP %s.",
        request.method,
        downstream_path,
        downstream_response.status_code,
    )
    content_type = downstream_response.headers.get("content-type", "application/json")
    return Response(
        content=downstream_response.content,
        status_code=downstream_response.status_code,
        headers={"content-type": content_type},
    )
