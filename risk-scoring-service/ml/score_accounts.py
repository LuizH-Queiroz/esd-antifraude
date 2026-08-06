"""Gera o risk score por conta a partir do classificador treinado.

Uso:
    python -m ml.score_accounts
    python -m ml.score_accounts --data ml/data/PS_20174392719_1491204439457_log.csv --top 30

Cada transação tem uma conta de origem (`nameOrig`) e uma de destino
(`nameDest`); no PaySim quase toda `nameOrig` aparece uma única vez (é
essencialmente o "cliente" daquela transação), enquanto `nameDest` se
repete bastante — por isso o score é calculado por *conta* (o mesmo ID
pode acumular papel de remetente em uma transação e destinatário em
outra), não só por `nameOrig`.

Para cada conta, agregamos a probabilidade de fraude (prevista pelo
classificador de `train.py`) de todas as transações em que ela aparece,
como remetente ou destinatária:

    risk_score = 1 - PRODUTO(1 - p_fraude_i) para toda transação i da conta

Ou seja, a probabilidade de que *pelo menos uma* das transações da conta
seja fraudulenta — cresce com o número de transações suspeitas
acumuladas, não só com o pico isolado. Resultado em escala 0-100.
"""

import argparse

import joblib
import numpy as np
import pandas as pd

from ml.config import (
    DEFAULT_ACCOUNT_SCORES_PATH,
    DEFAULT_DATA_PATH,
    DEFAULT_MODEL_PATH,
    HIGH_RISK_THRESHOLD,
)
from ml.data import load_transactions
from ml.features import build_transaction_features


def score_transactions(df: pd.DataFrame, model_path: str) -> pd.DataFrame:
    bundle = joblib.load(model_path)
    clf, feature_columns = bundle["model"], bundle["feature_columns"]
    df = build_transaction_features(df)
    df["p_fraud"] = clf.predict_proba(df[feature_columns])[:, 1]
    return df


def aggregate_account_scores(df: pd.DataFrame) -> pd.DataFrame:
    sender = df[["nameOrig", "p_fraud", "amount", "timestamp"]].rename(
        columns={"nameOrig": "account_id"}
    )
    sender["role"] = "sender"
    receiver = df[["nameDest", "p_fraud", "amount", "timestamp"]].rename(
        columns={"nameDest": "account_id"}
    )
    receiver["role"] = "receiver"
    involvement = pd.concat([sender, receiver], ignore_index=True)

    # log-espaço para estabilidade numérica em contas com muitas transações
    log_survival = np.log1p(-involvement["p_fraud"].clip(upper=0.999999))

    agg = involvement.groupby("account_id").agg(
        tx_count=("p_fraud", "size"),
        max_p_fraud=("p_fraud", "max"),
        mean_p_fraud=("p_fraud", "mean"),
        total_amount=("amount", "sum"),
        last_activity=("timestamp", "max"),
    )
    agg["risk_score"] = (
        1 - np.exp(log_survival.groupby(involvement["account_id"]).sum())
    ) * 100
    agg["high_risk_tx_count"] = (
        involvement[involvement["p_fraud"] >= HIGH_RISK_THRESHOLD]
        .groupby("account_id")
        .size()
        .reindex(agg.index, fill_value=0)
    )

    agg = agg.sort_values("risk_score", ascending=False).reset_index()
    agg["risk_score"] = agg["risk_score"].round(4)
    agg["max_p_fraud"] = agg["max_p_fraud"].round(4)
    agg["mean_p_fraud"] = agg["mean_p_fraud"].round(4)
    return agg[
        [
            "account_id",
            "risk_score",
            "tx_count",
            "high_risk_tx_count",
            "max_p_fraud",
            "mean_p_fraud",
            "total_amount",
            "last_activity",
        ]
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA_PATH))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--output", default=str(DEFAULT_ACCOUNT_SCORES_PATH))
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    print(f"Carregando transações de {args.data} ...")
    df = load_transactions(args.data)
    print(f"Pontuando {len(df):,} transações com o modelo em {args.model} ...")
    df = score_transactions(df, args.model)

    print("Agregando risk score por conta (remetente + destinatária)...")
    account_scores = aggregate_account_scores(df)
    account_scores.to_csv(args.output, index=False)
    print(f"\n{len(account_scores):,} contas pontuadas. CSV salvo em {args.output}\n")

    print(f"Top {args.top} contas de maior risco:")
    with pd.option_context("display.width", 120):
        print(account_scores.head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
