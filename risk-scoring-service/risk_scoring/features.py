"""Features "sem estado" (calculáveis a partir de uma única transação).

Equivalente, para uma única linha, à parte de ml/features.py que não
depende de histórico por conta: amount_log, hour_of_day, day_index e o
one-hot de `type`. As features COM estado (orig_prior_*, dest_prior_*,
*_seen_as_*) não estão aqui — dependem do histórico por conta, mantido em
account_stats.py, e são combinadas com estas em scoring.py.

As constantes (SIMULATION_START, TRANSACTION_TYPES) vêm diretamente de
ml.config — não são duplicadas. Isso é possível porque ml/ vive dentro
deste mesmo serviço (risk-scoring-service/ml/), então faz parte do mesmo
contexto de build Docker; ao contrário do schema HTTP compartilhado entre
api-gateway/ingestion-service (que SÃO serviços/deploys diferentes, e por
isso mantêm cópias próprias do contrato), aqui não há razão para duplicar
— é código do mesmo serviço.

Ainda assim, tests/test_feature_parity.py compara build_stateless_features
com ml.features.build_transaction_features sobre as mesmas entradas — não
para pegar uma constante desatualizada (não existe mais essa cópia), mas
para garantir que esta reimplementação escalar (linha a linha, sem pandas)
continua batendo com o pipeline em lote usado no treino.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from ml.config import SIMULATION_START, TRANSACTION_TYPES


def step_to_timestamp(step: int) -> datetime:
    """Converte o `step` do PaySim (1-indexado, 1h por step) em timestamp.

    Idêntica à fórmula de ml/data.py:
        SIMULATION_START + pd.to_timedelta(step - 1, unit="h")
    """
    return SIMULATION_START + timedelta(hours=step - 1)


def build_stateless_features(step: int, transaction_type: str, amount: float) -> dict:
    """Calcula as features que não dependem de histórico por conta."""
    timestamp = step_to_timestamp(step)

    features: dict[str, float | int] = {
        "amount": amount,
        "amount_log": math.log1p(amount),
        "hour_of_day": timestamp.hour,
        "day_index": (timestamp - SIMULATION_START).days,
    }
    for candidate_type in TRANSACTION_TYPES:
        features[f"type_{candidate_type}"] = 1 if transaction_type == candidate_type else 0

    return features