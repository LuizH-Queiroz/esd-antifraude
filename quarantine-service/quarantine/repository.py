"""Repositório de estado do Quarantine Service.

Este módulo implementa uma persistência simples em memória para manter o
serviço funcional e testável enquanto o fluxo completo de integração com o
PostgreSQL ainda não está totalmente consolidado. A estrutura foi pensada de
forma a permitir evolução futura para um repositório persistente sem alterar
o contrato das operações de negócio.

A principal regra aqui é preservar o estado oficial da conta e registrar os
comandos já processados para garantir idempotência quando uma mensagem do
RabbitMQ chega mais de uma vez.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AccountState:
    account_id: str
    status: str
    motivo: str | None = None
    risk_score: float | None = None


class InMemoryRepository:
    def __init__(self) -> None:
        self._states: dict[str, AccountState] = {}
        self._processed_commands: set[str] = set()

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    def get(self, account_id: str) -> AccountState:
        if account_id not in self._states:
            raise KeyError(account_id)
        return self._states[account_id]

    def upsert(self, state: AccountState) -> None:
        self._states[state.account_id] = state

    def set_quarantined(self, account_id: str, risk_score: float | None, motivo: str | None) -> None:
        self._states[account_id] = AccountState(
            account_id=account_id,
            status="EM_QUARENTENA",
            motivo=motivo,
            risk_score=risk_score,
        )

    def set_released(self, account_id: str, motivo: str | None = None) -> None:
        current_state = self._states.get(account_id)
        self._states[account_id] = AccountState(
            account_id=account_id,
            status="LIBERADA",
            motivo=motivo or (current_state.motivo if current_state else None),
            risk_score=current_state.risk_score if current_state else None,
        )

    def process_release_command(self, payload: dict[str, Any]) -> bool:
        """Processa um comando de liberação com idempotência por ``event_id``."""

        event_id = str(payload.get("event_id") or "").strip()
        if not event_id or event_id in self._processed_commands:
            return False

        account_id = str(payload.get("account_id") or "").strip()
        if not account_id:
            return False

        self._processed_commands.add(event_id)
        self.set_released(account_id, payload.get("motivo"))
        return True
