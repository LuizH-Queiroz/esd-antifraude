"""Testes automatizados do Risk Scoring Service.

Usam InMemoryAccountStatsStore e um modelo fake — não dependem de
PostgreSQL, RabbitMQ nem de um .joblib treinado de verdade. Cobrem a
lógica de orquestração (scoring.py) e o esquema de estado incremental
(account_stats.py) isoladamente.
"""

from __future__ import annotations

import asyncio

from ml.config import SIMULATION_START
from risk_scoring.account_stats import InMemoryAccountStatsStore
from risk_scoring.features import step_to_timestamp
from risk_scoring.scoring import process_transacao_registrada


class FakeFraudModel:
    """Modelo fake: sempre devolve um p_fraud fixo, sem carregar .joblib."""

    def __init__(self, fixed_p_fraud: float = 0.8) -> None:
        self.fixed_p_fraud = fixed_p_fraud
        self.received_features: list[dict] = []

    def predict_proba(self, features: dict) -> float:
        self.received_features.append(features)
        return self.fixed_p_fraud


EVENT = {
    "event_id": "evt-1",
    "transaction": {
        "step": 5,
        "type": "TRANSFER",
        "amount": 1000.0,
        "origin_account": "C1",
        "destination_account": "C2",
    },
}


def test_evento_atualiza_estado_das_duas_contas() -> None:
    async def _run():
        store = InMemoryAccountStatsStore()
        model = FakeFraudModel(fixed_p_fraud=0.8)

        p_fraud = await process_transacao_registrada(EVENT, store, model, high_risk_threshold=0.5)
        assert p_fraud == 0.8

        origin_score = await store.get_score("C1")
        destination_score = await store.get_score("C2")

        assert origin_score is not None
        assert destination_score is not None
        assert origin_score.tx_count == 1
        assert destination_score.tx_count == 1
        assert origin_score.high_risk_tx_count == 1  # 0.8 >= 0.5 (threshold default)

    asyncio.run(_run())


def test_segunda_transacao_reflete_historico_da_primeira() -> None:
    async def _run():
        store = InMemoryAccountStatsStore()
        model = FakeFraudModel(fixed_p_fraud=0.1)

        await process_transacao_registrada(EVENT, store, model, high_risk_threshold=0.5)
        await process_transacao_registrada(EVENT, store, model, high_risk_threshold=0.5)

        # Na segunda chamada, C1/C2 já tinham 1 transação anterior cada.
        second_call_features = model.received_features[1]
        assert second_call_features["orig_prior_tx_count"] == 1
        assert second_call_features["dest_prior_tx_count"] == 1

    asyncio.run(_run())


def test_step_to_timestamp_bate_com_a_ancora_para_step_1() -> None:
    # step=1 deve cair exatamente em SIMULATION_START (ver o "-1" na fórmula,
    # já que step é 1-indexado no PaySim).
    assert step_to_timestamp(1) == SIMULATION_START