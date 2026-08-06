"""Rota de liveness do Admin Panel Service."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "admin-panel-service"}
