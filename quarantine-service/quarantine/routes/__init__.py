"""Rotas do Quarantine Service."""

from quarantine.routes.health import router as health_router
from quarantine.routes.internal import router as internal_router

__all__ = ["health_router", "internal_router"]
