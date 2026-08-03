"""Event Store: persistência append-only dos eventos recebidos (PostgreSQL).

Implementa o padrão Event Sourcing citado na ADR 002 do README principal:
o histórico de eventos é a fonte da verdade auditável, não um "estado
atual" que sobrescreve o anterior — por isso a única operação de escrita
é um INSERT (nunca UPDATE/DELETE).

Duas implementações:
  - PostgresEventStore: a de produção, fala com o `ingestion-db` via
    asyncpg (driver assíncrono nativo, sem depender de threads).
  - InMemoryEventStore: usada apenas em testes automatizados (ver
    tests/test_ingestion_service.py na raiz do repositório), para
    verificar a lógica de negócio sem precisar de um PostgreSQL real.

Ambas implementam o mesmo "contrato" (EventStore), então o resto do
serviço (rotas) não sabe nem precisa saber qual das duas está em uso.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

import asyncpg

from ingestion.domain import TransacaoRegistrada

LOGGER = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_INSERT_EVENT_SQL = """
INSERT INTO events (event_id, event_type, payload, occurred_at)
VALUES ($1, $2, $3::jsonb, $4)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id;
"""


class EventStore(Protocol):
    """Contrato: qualquer coisa que saiba persistir e checar duplicidade serve aqui."""

    async def initialize(self) -> None: ...

    async def append(self, event: TransacaoRegistrada) -> bool:
        """Persiste o evento. Retorna True se foi uma inserção nova,
        False se o event_id já existia (evento duplicado — ver decisão de
        idempotência na Issue)."""
        ...

    async def close(self) -> None: ...


class PostgresEventStore:
    """Implementação real, sobre PostgreSQL via asyncpg."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        # min_size=1 evita o custo de abrir conexão a cada request; max_size
        # baixo é suficiente para o volume de uma POC (ajustar se necessário).
        self._pool = await asyncpg.create_pool(dsn=self._database_url, min_size=1, max_size=5)
        async with self._pool.acquire() as connection:
            # CREATE TABLE IF NOT EXISTS no startup, em vez de uma ferramenta
            # de migração formal (ex.: Alembic): suficiente para uma única
            # tabela sem relações, mantendo o serviço simples de entender.
            # Se o esquema crescer, vale reconsiderar uma ferramenta de
            # migração dedicada.
            await connection.execute(_CREATE_TABLE_SQL)
        LOGGER.info("Conectado ao PostgreSQL e tabela 'events' garantida.")

    async def append(self, event: TransacaoRegistrada) -> bool:
        assert self._pool is not None, "initialize() precisa ser chamado antes."

        payload = json.dumps(event.to_message())
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                _INSERT_EVENT_SQL,
                event.event_id,
                "TransacaoRegistrada",
                payload,
                event.occurred_at,
            )
        return row is not None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


class InMemoryEventStore:
    """Fake em memória, usado apenas em testes automatizados."""

    def __init__(self) -> None:
        self.events: dict[str, TransacaoRegistrada] = {}

    async def initialize(self) -> None:
        pass  # nada a preparar

    async def append(self, event: TransacaoRegistrada) -> bool:
        if event.event_id in self.events:
            return False
        self.events[event.event_id] = event
        return True

    async def close(self) -> None:
        pass  # nada a fechar