"""Estado incremental por conta: histórico causal + risk score acumulado.

Reproduz, de forma incremental (uma transação de cada vez, orientada a
eventos), o que ml/features.py calcula em lote sobre o CSV inteiro
(cumcount/cumsum causal) e o que ml/score_accounts.py calcula depois do
scoring (risk_score = 1 - produto(1 - p_fraude) de todas as transações da
conta, seja como remetente ou destinatária). Ver a explicação completa no
README deste serviço.

IMPORTANTE — pré-requisito de ordem: este esquema só é causal (só reflete
o passado real de cada conta) se as transações forem processadas uma de
cada vez, na mesma ordem em que ocorreram. O consumidor RabbitMQ deste
serviço (consumer.py) é deliberadamente sequencial por isso — não há
paralelismo de processamento. Ver a nota de limitação/escalabilidade no
README.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import asyncpg

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS account_stats (
    account_id TEXT PRIMARY KEY,
    tx_count_as_orig INTEGER NOT NULL DEFAULT 0,
    amount_sum_as_orig DOUBLE PRECISION NOT NULL DEFAULT 0,
    first_seen_as_orig_at TIMESTAMPTZ,
    tx_count_as_dest INTEGER NOT NULL DEFAULT 0,
    amount_sum_as_dest DOUBLE PRECISION NOT NULL DEFAULT 0,
    first_seen_as_dest_at TIMESTAMPTZ,
    log_survival_sum DOUBLE PRECISION NOT NULL DEFAULT 0,
    p_fraud_sum DOUBLE PRECISION NOT NULL DEFAULT 0,
    involvement_count INTEGER NOT NULL DEFAULT 0,
    max_p_fraud DOUBLE PRECISION NOT NULL DEFAULT 0,
    high_risk_tx_count INTEGER NOT NULL DEFAULT 0,
    total_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_activity TIMESTAMPTZ
);
"""

# As duas queries de upsert (uma para o papel "origem", outra para
# "destino") são quase idênticas, variando só quais colunas de papel são
# tocadas. Definidas como templates para não duplicar a lógica compartilhada
# (log_survival_sum, p_fraud_sum, max_p_fraud, etc.) em dois lugares.
_UPSERT_AS_ORIGIN_SQL = """
INSERT INTO account_stats (
    account_id, tx_count_as_orig, amount_sum_as_orig, first_seen_as_orig_at,
    log_survival_sum, p_fraud_sum, involvement_count, max_p_fraud,
    high_risk_tx_count, total_amount, last_activity
) VALUES ($1, 1, $2, $3, $4, $5, 1, $5, $6, $2, $3)
ON CONFLICT (account_id) DO UPDATE SET
    tx_count_as_orig = account_stats.tx_count_as_orig + 1,
    amount_sum_as_orig = account_stats.amount_sum_as_orig + EXCLUDED.amount_sum_as_orig,
    first_seen_as_orig_at = COALESCE(
        account_stats.first_seen_as_orig_at, EXCLUDED.first_seen_as_orig_at
    ),
    log_survival_sum = account_stats.log_survival_sum + EXCLUDED.log_survival_sum,
    p_fraud_sum = account_stats.p_fraud_sum + EXCLUDED.p_fraud_sum,
    involvement_count = account_stats.involvement_count + 1,
    max_p_fraud = GREATEST(account_stats.max_p_fraud, EXCLUDED.max_p_fraud),
    high_risk_tx_count = account_stats.high_risk_tx_count + EXCLUDED.high_risk_tx_count,
    total_amount = account_stats.total_amount + EXCLUDED.total_amount,
    last_activity = GREATEST(account_stats.last_activity, EXCLUDED.last_activity);
"""

_UPSERT_AS_DESTINATION_SQL = """
INSERT INTO account_stats (
    account_id, tx_count_as_dest, amount_sum_as_dest, first_seen_as_dest_at,
    log_survival_sum, p_fraud_sum, involvement_count, max_p_fraud,
    high_risk_tx_count, total_amount, last_activity
) VALUES ($1, 1, $2, $3, $4, $5, 1, $5, $6, $2, $3)
ON CONFLICT (account_id) DO UPDATE SET
    tx_count_as_dest = account_stats.tx_count_as_dest + 1,
    amount_sum_as_dest = account_stats.amount_sum_as_dest + EXCLUDED.amount_sum_as_dest,
    first_seen_as_dest_at = COALESCE(
        account_stats.first_seen_as_dest_at, EXCLUDED.first_seen_as_dest_at
    ),
    log_survival_sum = account_stats.log_survival_sum + EXCLUDED.log_survival_sum,
    p_fraud_sum = account_stats.p_fraud_sum + EXCLUDED.p_fraud_sum,
    involvement_count = account_stats.involvement_count + 1,
    max_p_fraud = GREATEST(account_stats.max_p_fraud, EXCLUDED.max_p_fraud),
    high_risk_tx_count = account_stats.high_risk_tx_count + EXCLUDED.high_risk_tx_count,
    total_amount = account_stats.total_amount + EXCLUDED.total_amount,
    last_activity = GREATEST(account_stats.last_activity, EXCLUDED.last_activity);
"""

@dataclass(frozen=True, slots=True)
class AccountState:
    """Histórico de uma conta ANTES da transação atual.

    Usado para montar as features causais (orig_prior_*, dest_prior_*,
    *_seen_as_*_before) de uma nova transação — nunca inclui a própria
    transação que está sendo processada.
    """

    tx_count_as_orig: int = 0
    amount_sum_as_orig: float = 0.0
    first_seen_as_orig_at: datetime | None = None
    tx_count_as_dest: int = 0
    amount_sum_as_dest: float = 0.0
    first_seen_as_dest_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountScore:
    """Risk score agregado de uma conta — mesmo formato de saída de
    ml/score_accounts.py (account_id, risk_score, tx_count, ...), só que
    calculado incrementalmente em vez de em lote."""

    account_id: str
    risk_score: float
    tx_count: int
    high_risk_tx_count: int
    max_p_fraud: float
    mean_p_fraud: float
    total_amount: float
    last_activity: datetime | None


class AccountStatsStore(Protocol):
    async def initialize(self) -> None: ...

    async def get_state(self, account_id: str) -> AccountState: ...

    async def record_transaction(
        self,
        *,
        origin_account: str,
        destination_account: str,
        amount: float,
        p_fraud: float,
        event_timestamp: datetime,
        high_risk_threshold: float,
    ) -> None: ...

    async def get_score(self, account_id: str) -> AccountScore | None: ...

    async def close(self) -> None: ...


class PostgresAccountStatsStore:
    """Implementação real, sobre PostgreSQL via asyncpg."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        self._pool = await asyncpg.create_pool(dsn=self._database_url, min_size=1, max_size=5)
        async with self._pool.acquire() as connection:
            await connection.execute(_CREATE_TABLE_SQL)

    async def get_state(self, account_id: str) -> AccountState:
        assert self._pool is not None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT tx_count_as_orig, amount_sum_as_orig, first_seen_as_orig_at,
                       tx_count_as_dest, amount_sum_as_dest, first_seen_as_dest_at
                FROM account_stats WHERE account_id = $1
                """,
                account_id,
            )
        if row is None:
            return AccountState()
        return AccountState(**dict(row))

    async def record_transaction(
        self,
        *,
        origin_account: str,
        destination_account: str,
        amount: float,
        p_fraud: float,
        event_timestamp: datetime,
        high_risk_threshold: float,
    ) -> None:
        assert self._pool is not None

        # clip evita log(0) = -inf no caso extremo de p_fraud=1.0 exato.
        log_survival_delta = math.log1p(-min(p_fraud, 0.999999))
        high_risk_delta = 1 if p_fraud >= high_risk_threshold else 0

        # As duas contas (origem e destino) são atualizadas na MESMA
        # transação de banco — se uma falhar, a outra também não é
        # aplicada, evitando um estado inconsistente onde só uma conta
        # "sabe" que participou desta transação.
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(
                _UPSERT_AS_ORIGIN_SQL,
                origin_account, amount, event_timestamp,
                log_survival_delta, p_fraud, high_risk_delta,
            )
            await connection.execute(
                _UPSERT_AS_DESTINATION_SQL,
                destination_account, amount, event_timestamp,
                log_survival_delta, p_fraud, high_risk_delta,
            )

    async def get_score(self, account_id: str) -> AccountScore | None:
        assert self._pool is not None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT log_survival_sum, involvement_count, high_risk_tx_count,
                       max_p_fraud, p_fraud_sum, total_amount, last_activity
                FROM account_stats WHERE account_id = $1
                """,
                account_id,
            )
        if row is None:
            return None
        return _row_to_score(account_id, dict(row))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def _row_to_score(account_id: str, row: dict) -> AccountScore:
    involvement_count = row["involvement_count"]
    mean_p_fraud = row["p_fraud_sum"] / involvement_count if involvement_count else 0.0
    # A mesma fórmula de ml/score_accounts.py, só que aplicada
    # incrementalmente: log_survival_sum já é a soma de log1p(-p_fraud)
    # acumulada evento a evento, então exp() + inversão dá o mesmo
    # resultado que rodar o produtório de uma vez sobre o histórico
    # completo.
    risk_score = (1 - math.exp(row["log_survival_sum"])) * 100

    return AccountScore(
        account_id=account_id,
        risk_score=round(risk_score, 4),
        tx_count=involvement_count,
        high_risk_tx_count=row["high_risk_tx_count"],
        max_p_fraud=round(row["max_p_fraud"], 4),
        mean_p_fraud=round(mean_p_fraud, 4),
        total_amount=row["total_amount"],
        last_activity=row["last_activity"],
    )


class InMemoryAccountStatsStore:
    """Fake em memória, usado apenas em testes automatizados."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def initialize(self) -> None:
        pass

    def _row(self, account_id: str) -> dict:
        return self._rows.setdefault(
            account_id,
            {
                "tx_count_as_orig": 0, "amount_sum_as_orig": 0.0, "first_seen_as_orig_at": None,
                "tx_count_as_dest": 0, "amount_sum_as_dest": 0.0, "first_seen_as_dest_at": None,
                "log_survival_sum": 0.0, "p_fraud_sum": 0.0, "involvement_count": 0,
                "max_p_fraud": 0.0, "high_risk_tx_count": 0, "total_amount": 0.0,
                "last_activity": None,
            },
        )

    async def get_state(self, account_id: str) -> AccountState:
        row = self._rows.get(account_id)
        if row is None:
            return AccountState()
        return AccountState(
            tx_count_as_orig=row["tx_count_as_orig"],
            amount_sum_as_orig=row["amount_sum_as_orig"],
            first_seen_as_orig_at=row["first_seen_as_orig_at"],
            tx_count_as_dest=row["tx_count_as_dest"],
            amount_sum_as_dest=row["amount_sum_as_dest"],
            first_seen_as_dest_at=row["first_seen_as_dest_at"],
        )

    async def record_transaction(
        self,
        *,
        origin_account: str,
        destination_account: str,
        amount: float,
        p_fraud: float,
        event_timestamp: datetime,
        high_risk_threshold: float,
    ) -> None:
        log_survival_delta = math.log1p(-min(p_fraud, 0.999999))
        high_risk_delta = 1 if p_fraud >= high_risk_threshold else 0

        orig = self._row(origin_account)
        orig["tx_count_as_orig"] += 1
        orig["amount_sum_as_orig"] += amount
        if orig["first_seen_as_orig_at"] is None:
            orig["first_seen_as_orig_at"] = event_timestamp
        self._apply_shared(
            orig, amount, p_fraud, event_timestamp, log_survival_delta, high_risk_delta
        )

        dest = self._row(destination_account)
        dest["tx_count_as_dest"] += 1
        dest["amount_sum_as_dest"] += amount
        if dest["first_seen_as_dest_at"] is None:
            dest["first_seen_as_dest_at"] = event_timestamp
        self._apply_shared(
            dest, amount, p_fraud, event_timestamp, log_survival_delta, high_risk_delta
        )

    @staticmethod
    def _apply_shared(row, amount, p_fraud, event_timestamp, log_survival_delta, high_risk_delta):
        row["log_survival_sum"] += log_survival_delta
        row["p_fraud_sum"] += p_fraud
        row["involvement_count"] += 1
        row["max_p_fraud"] = max(row["max_p_fraud"], p_fraud)
        row["high_risk_tx_count"] += high_risk_delta
        row["total_amount"] += amount
        if row["last_activity"] is None or event_timestamp > row["last_activity"]:
            row["last_activity"] = event_timestamp

    async def get_score(self, account_id: str) -> AccountScore | None:
        row = self._rows.get(account_id)
        if row is None:
            return None
        return _row_to_score(account_id, row)

    async def close(self) -> None:
        pass