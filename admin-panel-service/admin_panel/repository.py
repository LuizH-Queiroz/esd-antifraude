"""Persistência da projeção consultável e do histórico append-only."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

from admin_panel.domain import (
    AuditEvent,
    CasePage,
    CaseRecord,
    CaseStatus,
    QuarantineEvent,
    ReleaseCommand,
)

_CREATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
    account_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('EM_QUARENTENA', 'LIBERADA')),
    risk_score DOUBLE PRECISION,
    motivo TEXT,
    quarantined_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cases_status_updated_at
    ON cases (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_account_occurred_at
    ON events (account_id, occurred_at ASC);
"""

_INSERT_EVENT_SQL = """
INSERT INTO events (event_id, account_id, event_type, payload, occurred_at)
VALUES ($1, $2, $3, $4::jsonb, $5)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id;
"""


class CaseRepository(Protocol):
    async def initialize(self) -> None: ...

    async def apply_quarantine_event(self, event: QuarantineEvent) -> bool: ...

    async def append_release_command(self, command: ReleaseCommand) -> bool: ...

    async def list_cases(
        self,
        *,
        status: CaseStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        min_score: float | None,
        max_score: float | None,
        page: int,
        page_size: int,
    ) -> CasePage: ...

    async def get_case(self, account_id: str) -> CaseRecord | None: ...

    async def close(self) -> None: ...


class PostgresCaseRepository:
    """Implementação de produção usando PostgreSQL via asyncpg."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any | None = None

    async def initialize(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(
            dsn=self._database_url,
            min_size=1,
            max_size=5,
        )
        async with self._pool.acquire() as connection:
            await connection.execute(_CREATE_SCHEMA_SQL)

    async def apply_quarantine_event(self, event: QuarantineEvent) -> bool:
        assert self._pool is not None, "initialize() precisa ser chamado antes."
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                inserted = await connection.fetchrow(
                    _INSERT_EVENT_SQL,
                    event.event_id,
                    event.account_id,
                    event.event_type,
                    json.dumps(event.payload),
                    event.occurred_at,
                )
                if inserted is None:
                    return False

                if event.event_type == "ContaEmQuarentena":
                    await connection.execute(
                        """
                        INSERT INTO cases (
                            account_id, status, risk_score, motivo,
                            quarantined_at, updated_at
                        )
                        VALUES ($1, 'EM_QUARENTENA', $2, $3, $4, $4)
                        ON CONFLICT (account_id) DO UPDATE SET
                            status = 'EM_QUARENTENA',
                            risk_score = EXCLUDED.risk_score,
                            motivo = EXCLUDED.motivo,
                            quarantined_at = EXCLUDED.quarantined_at,
                            updated_at = EXCLUDED.updated_at;
                        """,
                        event.account_id,
                        event.risk_score,
                        event.motivo,
                        event.occurred_at,
                    )
                else:
                    await connection.execute(
                        """
                        INSERT INTO cases (
                            account_id, status, risk_score, motivo,
                            quarantined_at, updated_at
                        )
                        VALUES ($1, 'LIBERADA', $2, $3, NULL, $4)
                        ON CONFLICT (account_id) DO UPDATE SET
                            status = 'LIBERADA',
                            updated_at = EXCLUDED.updated_at;
                        """,
                        event.account_id,
                        event.risk_score,
                        event.motivo,
                        event.occurred_at,
                    )
                return True

    async def append_release_command(self, command: ReleaseCommand) -> bool:
        assert self._pool is not None, "initialize() precisa ser chamado antes."
        payload = command.to_message()
        async with self._pool.acquire() as connection:
            inserted = await connection.fetchrow(
                _INSERT_EVENT_SQL,
                command.command_id,
                command.account_id,
                "ComandoDeLiberacao",
                json.dumps(payload),
                command.occurred_at,
            )
        return inserted is not None

    async def list_cases(
        self,
        *,
        status: CaseStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        min_score: float | None,
        max_score: float | None,
        page: int,
        page_size: int,
    ) -> CasePage:
        assert self._pool is not None, "initialize() precisa ser chamado antes."

        conditions: list[str] = []
        values: list[Any] = []

        def add_condition(template: str, value: Any) -> None:
            values.append(value)
            conditions.append(template.format(index=len(values)))

        if status is not None:
            add_condition("status = ${index}", status.value)
        if date_from is not None:
            add_condition("quarantined_at >= ${index}", date_from)
        if date_to is not None:
            add_condition("quarantined_at <= ${index}", date_to)
        if min_score is not None:
            add_condition("risk_score >= ${index}", min_score)
        if max_score is not None:
            add_condition("risk_score <= ${index}", max_score)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * page_size
        values.extend([page_size, offset])
        limit_index = len(values) - 1
        offset_index = len(values)

        async with self._pool.acquire() as connection:
            total = await connection.fetchval(
                f"SELECT COUNT(*) FROM cases {where_sql};",
                *values[: limit_index - 1],
            )
            rows = await connection.fetch(
                f"""
                SELECT account_id, status, risk_score, motivo,
                       quarantined_at, updated_at
                FROM cases
                {where_sql}
                ORDER BY updated_at DESC, account_id ASC
                LIMIT ${limit_index} OFFSET ${offset_index};
                """,
                *values,
            )

        return CasePage(
            items=[self._case_from_row(row) for row in rows],
            total=int(total),
            page=page,
            page_size=page_size,
        )

    async def get_case(self, account_id: str) -> CaseRecord | None:
        assert self._pool is not None, "initialize() precisa ser chamado antes."
        async with self._pool.acquire() as connection:
            case_row = await connection.fetchrow(
                """
                SELECT account_id, status, risk_score, motivo,
                       quarantined_at, updated_at
                FROM cases
                WHERE account_id = $1;
                """,
                account_id,
            )
            if case_row is None:
                return None

            event_rows = await connection.fetch(
                """
                SELECT event_id, event_type, payload, occurred_at
                FROM events
                WHERE account_id = $1
                ORDER BY occurred_at ASC, recorded_at ASC;
                """,
                account_id,
            )

        events = [self._audit_event_from_row(row) for row in event_rows]
        case = self._case_from_row(case_row)
        return CaseRecord(
            account_id=case.account_id,
            status=case.status,
            risk_score=case.risk_score,
            motivo=case.motivo,
            quarantined_at=case.quarantined_at,
            updated_at=case.updated_at,
            events=events,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @staticmethod
    def _case_from_row(row: Any) -> CaseRecord:
        return CaseRecord(
            account_id=row["account_id"],
            status=CaseStatus(row["status"]),
            risk_score=row["risk_score"],
            motivo=row["motivo"],
            quarantined_at=row["quarantined_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _audit_event_from_row(row: Any) -> AuditEvent:
        payload = PostgresCaseRepository._normalize_payload(row["payload"])
        return AuditEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            occurred_at=row["occurred_at"],
            actor=payload.get("requested_by") or payload.get("released_by"),
            motivo=payload.get("motivo"),
            payload=payload,
        )

    @staticmethod
    def _normalize_payload(raw_payload: Any) -> dict[str, Any]:
        if isinstance(raw_payload, dict):
            return dict(raw_payload)

        if isinstance(raw_payload, str):
            parsed_payload = json.loads(raw_payload)
            if not isinstance(parsed_payload, dict):
                raise TypeError("payload JSON precisa ser um objeto.")
            return parsed_payload

        raise TypeError(
            "payload precisa ser dict ou string JSON, recebido "
            f"{type(raw_payload).__name__}."
        )


class InMemoryCaseRepository:
    """Implementação simples usada pelos testes automatizados."""

    def __init__(self) -> None:
        self.cases: dict[str, CaseRecord] = {}
        self.events: dict[str, tuple[str, AuditEvent]] = {}

    async def initialize(self) -> None:
        pass

    async def apply_quarantine_event(self, event: QuarantineEvent) -> bool:
        if event.event_id in self.events:
            return False

        audit = AuditEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            actor=event.released_by,
            motivo=event.motivo,
            payload=event.payload,
        )
        self.events[event.event_id] = (event.account_id, audit)
        previous = self.cases.get(event.account_id)

        if event.event_type == "ContaEmQuarentena":
            self.cases[event.account_id] = CaseRecord(
                account_id=event.account_id,
                status=CaseStatus.EM_QUARENTENA,
                risk_score=event.risk_score,
                motivo=event.motivo,
                quarantined_at=event.occurred_at,
                updated_at=event.occurred_at,
            )
        else:
            self.cases[event.account_id] = CaseRecord(
                account_id=event.account_id,
                status=CaseStatus.LIBERADA,
                risk_score=previous.risk_score if previous else event.risk_score,
                motivo=previous.motivo if previous else event.motivo,
                quarantined_at=previous.quarantined_at if previous else None,
                updated_at=event.occurred_at,
            )
        return True

    async def append_release_command(self, command: ReleaseCommand) -> bool:
        if command.command_id in self.events:
            return False
        payload = command.to_message()
        self.events[command.command_id] = (
            command.account_id,
            AuditEvent(
                event_id=command.command_id,
                event_type="ComandoDeLiberacao",
                occurred_at=command.occurred_at,
                actor=command.requested_by,
                motivo=command.motivo,
                payload=payload,
            ),
        )
        return True

    async def list_cases(
        self,
        *,
        status: CaseStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        min_score: float | None,
        max_score: float | None,
        page: int,
        page_size: int,
    ) -> CasePage:
        items = list(self.cases.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if date_from is not None:
            items = [
                item
                for item in items
                if item.quarantined_at is not None and item.quarantined_at >= date_from
            ]
        if date_to is not None:
            items = [
                item
                for item in items
                if item.quarantined_at is not None and item.quarantined_at <= date_to
            ]
        if min_score is not None:
            items = [
                item
                for item in items
                if item.risk_score is not None and item.risk_score >= min_score
            ]
        if max_score is not None:
            items = [
                item
                for item in items
                if item.risk_score is not None and item.risk_score <= max_score
            ]

        items.sort(key=lambda item: (item.updated_at, item.account_id), reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return CasePage(
            items=items[start : start + page_size],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_case(self, account_id: str) -> CaseRecord | None:
        case = self.cases.get(account_id)
        if case is None:
            return None
        events = [
            audit
            for event_account_id, audit in self.events.values()
            if event_account_id == account_id
        ]
        events.sort(key=lambda event: event.occurred_at)
        return CaseRecord(
            account_id=case.account_id,
            status=case.status,
            risk_score=case.risk_score,
            motivo=case.motivo,
            quarantined_at=case.quarantined_at,
            updated_at=case.updated_at,
            events=events,
        )

    async def close(self) -> None:
        pass
