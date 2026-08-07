"""Processamento dos eventos recebidos do RabbitMQ."""

from __future__ import annotations

import logging
from typing import Any

from admin_panel.domain import QuarantineEvent
from admin_panel.repository import CaseRepository
from admin_panel.schemas import QuarantineBrokerEvent

LOGGER = logging.getLogger(__name__)


class QuarantineEventProcessor:
    def __init__(self, repository: CaseRepository) -> None:
        self._repository = repository

    async def handle(self, payload: dict[str, Any]) -> None:
        incoming = QuarantineBrokerEvent.model_validate(payload)
        event = QuarantineEvent.from_broker_event(incoming)
        await self._repository.apply_quarantine_event(event)
        LOGGER.info(
            "Evento %s processado para a conta %s.",
            incoming.event_type,
            event.account_id,
        )