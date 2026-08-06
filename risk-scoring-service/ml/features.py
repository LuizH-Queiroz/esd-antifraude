"""Engenharia de features por transação.

Reflete os 3 fatores multifatoriais do risk score citados no README raiz
do projeto que fazem sentido com as colunas priorizadas (step, type,
amount, nameOrig, nameDest):

- tipo de transação      -> one-hot de `type`
- valor                  -> `amount` / `amount_log`
- padrão temporal        -> `hour_of_day` / `day_index` (derivados do timestamp)
- correlação entre contas -> histórico causal de nameOrig/nameDest e
  indicadores de cadeia de repasse (conta que recebeu e depois enviou, ou
  vice-versa)

Todo agregado por conta é causal: usa `cumcount`/`cumsum` sobre o dataframe
ordenado por tempo, então só enxerga transações *anteriores* à linha atual
— sem vazar informação do futuro para o modelo.

Um segundo conjunto de features (fan-in/fan-out de contrapartes distintas e
horas desde a última movimentação da conta) foi testado e descartado — ver
"Experimentos" em ml/README.md: pioraram o PR-AUC em toda comparação feita
com o RandomForest atual, então não valeu manter esse código.
"""

import numpy as np
import pandas as pd

from ml.config import SIMULATION_START, TRANSACTION_TYPES

TYPE_COLUMNS = [f"type_{t}" for t in TRANSACTION_TYPES]

FEATURE_COLUMNS = [
    "amount",
    "amount_log",
    "hour_of_day",
    "day_index",
    "orig_prior_tx_count",
    "orig_prior_amount_sum",
    "dest_prior_tx_count",
    "dest_prior_amount_sum",
    "orig_seen_as_dest_before",
    "dest_seen_as_orig_before",
    *TYPE_COLUMNS,
]


def build_transaction_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # --- padrão temporal ---
    sim_start = SIMULATION_START
    df["hour_of_day"] = df["timestamp"].dt.hour
    df["day_index"] = (df["timestamp"] - sim_start).dt.days

    # --- valor ---
    df["amount_log"] = np.log1p(df["amount"])

    # --- correlação entre contas: histórico causal por papel ---
    df["orig_prior_tx_count"] = df.groupby("nameOrig", observed=True).cumcount()
    df["orig_prior_amount_sum"] = (
        df.groupby("nameOrig", observed=True)["amount"].cumsum() - df["amount"]
    )
    df["dest_prior_tx_count"] = df.groupby("nameDest", observed=True).cumcount()
    df["dest_prior_amount_sum"] = (
        df.groupby("nameDest", observed=True)["amount"].cumsum() - df["amount"]
    )

    # --- correlação entre contas: cadeias de repasse (troca de papel) ---
    # Ex.: conta que primeiro recebeu dinheiro (dest) e, depois, aparece
    # enviando (orig) — padrão clássico de "funil" em lavagem de dinheiro.
    first_as_dest_ts = df.groupby("nameDest", observed=True)["timestamp"].min()
    first_as_orig_ts = df.groupby("nameOrig", observed=True)["timestamp"].min()

    orig_first_dest_ts = df["nameOrig"].map(first_as_dest_ts)
    df["orig_seen_as_dest_before"] = (
        orig_first_dest_ts.notna() & (orig_first_dest_ts < df["timestamp"])
    ).astype("int8")

    dest_first_orig_ts = df["nameDest"].map(first_as_orig_ts)
    df["dest_seen_as_orig_before"] = (
        dest_first_orig_ts.notna() & (dest_first_orig_ts < df["timestamp"])
    ).astype("int8")

    # --- tipo de transação ---
    type_categorical = pd.Categorical(df["type"], categories=TRANSACTION_TYPES)
    type_dummies = pd.get_dummies(type_categorical, prefix="type").astype("int8")
    type_dummies.index = df.index
    df = pd.concat([df, type_dummies], axis=1)

    return df
