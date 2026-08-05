"""Compara as features "sem estado" do risk-scoring-service com as
calculadas pelo pipeline de treino (ml/features.py), sobre as mesmas
transações de exemplo.

Se este teste falhar, é sinal de que ml/config.py (ou ml/data.py) mudou
sem que risk_scoring/config.py (ou risk_scoring/features.py) fosse
atualizado junto — ver a explicação completa em
risk-scoring-service/risk_scoring/features.py.
"""

from __future__ import annotations

import pandas as pd
import pytest
from ml.config import SIMULATION_START, TRANSACTION_TYPES
from ml.features import build_transaction_features
from risk_scoring.features import build_stateless_features

SAMPLE_TRANSACTIONS = [
    {"step": 1, "type": "PAYMENT", "amount": 1060.31},
    {"step": 5, "type": "TRANSFER", "amount": 181000.0},
    {"step": 200, "type": "CASH_OUT", "amount": 5000.0},
    {"step": 743, "type": "CASH_IN", "amount": 250.75},
]


def _offline_features_for_one_row(row: dict) -> pd.Series:
    """Roda o caminho de treino (ml/features.py) sobre uma única linha,
    replicando a conversão step -> timestamp de ml/data.py."""
    df = pd.DataFrame(
        [{**row, "nameOrig": "A", "nameDest": "B"}]  # contas fictícias; não comparadas aqui
    )
    df["timestamp"] = SIMULATION_START + pd.to_timedelta(df["step"] - 1, unit="h")
    df = df.drop(columns=["step"])
    return build_transaction_features(df).iloc[0]


@pytest.mark.parametrize("row", SAMPLE_TRANSACTIONS)
def test_features_sem_estado_batem_com_o_pipeline_offline(row: dict) -> None:
    offline = _offline_features_for_one_row(row)
    online = build_stateless_features(row["step"], row["type"], row["amount"])

    assert online["amount_log"] == pytest.approx(offline["amount_log"])
    assert online["hour_of_day"] == offline["hour_of_day"]
    assert online["day_index"] == offline["day_index"]
    for transaction_type in TRANSACTION_TYPES:
        column = f"type_{transaction_type}"
        assert online[column] == offline[column]