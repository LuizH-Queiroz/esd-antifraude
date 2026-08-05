"""Carregamento do dataset PaySim e conversão de `step` em `timestamp`."""

from pathlib import Path

import pandas as pd

from ml.config import RAW_COLUMNS, SIMULATION_START


def load_transactions(csv_path: str | Path) -> pd.DataFrame:
    """Lê o CSV do PaySim mantendo apenas as colunas priorizadas.

    Ordena por `step` (ordem estável, preservando a ordem original em caso
    de empate) e substitui `step` por um `timestamp` real — cada step
    representa 1 hora de simulação a partir de `SIMULATION_START`. Essa
    ordenação cronológica é o que permite calcular, em `features.py`,
    agregados por conta que só olham para o passado de cada transação
    (sem vazamento de informação futura).
    """
    df = pd.read_csv(
        csv_path,
        usecols=RAW_COLUMNS,
        dtype={
            "step": "int32",
            "type": "category",
            "amount": "float64",
            "nameOrig": "string",
            "nameDest": "string",
            "isFraud": "int8",
        },
    )
    df = df.sort_values("step", kind="mergesort").reset_index(drop=True)
    df["timestamp"] = SIMULATION_START + pd.to_timedelta(df["step"] - 1, unit="h")
    df = df.drop(columns=["step"])
    return df
