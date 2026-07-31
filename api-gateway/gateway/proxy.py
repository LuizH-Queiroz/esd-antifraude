"""Encaminhamento (proxy) de requisições do Gateway para os serviços internos.

Esta é a peça que materializa o papel do API Gateway descrito no README
principal: "roteia as requisições externas ao sistema para os microsserviços
responsáveis por lidar com elas" — sem nunca permitir que quem está do lado
de fora (Sistema Bancário, Administrador) fale diretamente com um serviço
interno.

Como nenhum serviço interno (Ingestion Service, Admin Panel Service) está
implementado ainda, toda chamada feita por aqui vai falhar por conexão
recusada/timeout. Isso é esperado nesta fase do projeto — por isso as falhas
de rede são tratadas explicitamente e convertidas em HTTP 503 (Service
Unavailable) para quem chamou o Gateway, em vez de um erro 500 genérico ou o
processo travando. Não por acaso, 503 já é um dos códigos que o cliente HTTP
do simulador trata como retryable (ver simulator/app/client.py) — então, uma
vez que o Ingestion Service exista de fato, o mesmo simulador volta a
funcionar sem precisar de nenhuma mudança.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

LOGGER = logging.getLogger(__name__)


async def forward_json(
    http_client: httpx.AsyncClient,
    *,
    base_url: str,
    path: str,
    json_body: dict[str, Any],
    forwarded_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Encaminha um corpo JSON para um serviço interno via POST.

    Erros de conexão/timeout viram HTTPException(503). Qualquer resposta HTTP
    recebida do serviço interno (mesmo 4xx/5xx dele) é repassada como está —
    quem decide o que é um erro de domínio é o serviço de destino, não o
    Gateway.
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    try:
        response = await http_client.post(url, json=json_body, headers=forwarded_headers)
    except httpx.RequestError as exc:
        LOGGER.warning("Falha ao encaminhar requisição para %s: %s", url, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Serviço interno indisponível ao tentar encaminhar para {url}. "
                "Isso é esperado enquanto o serviço de destino ainda não foi "
                "implementado ou não está no ar."
            ),
        ) from exc

    return response


async def forward_request(
    http_client: httpx.AsyncClient,
    *,
    base_url: str,
    path: str,
    method: str,
    query_params: dict[str, Any] | None = None,
    json_body: Any | None = None,
    forwarded_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Versão genérica de `forward_json`, usada pelas rotas administrativas.

    Aceita qualquer método HTTP, já que o Painel Admin (futuro Admin Panel
    Service) precisará tanto consultar (GET) quanto comandar (POST/PATCH)
    o sistema — ver o papel do Administrador no C4 de Nível 2.
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    try:
        response = await http_client.request(
            method,
            url,
            params=query_params,
            json=json_body,
            headers=forwarded_headers,
        )
    except httpx.RequestError as exc:
        LOGGER.warning("Falha ao encaminhar requisição para %s: %s", url, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Serviço interno indisponível ao tentar encaminhar para {url}. "
                "Isso é esperado enquanto o serviço de destino ainda não foi "
                "implementado ou não está no ar."
            ),
        ) from exc

    return response