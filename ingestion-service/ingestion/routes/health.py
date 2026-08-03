"""Rota de verificação de saúde (liveness) do Ingestion Service.

Deliberadamente simples (não verifica conexão com banco/broker aqui): é a
mesma rota que o /health/dependencies do API Gateway consulta para saber
se o Ingestion Service está de pé (ver api-gateway/gateway/routes/health.py).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict:
    return {"status": "ok", "service": "ingestion-service"}