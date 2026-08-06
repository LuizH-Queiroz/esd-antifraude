"""Treina o classificador de fraude por transação (base do risk score).

Uso:
    python -m ml.train
    python -m ml.train --data ml/data/PS_20174392719_1491204439457_log.csv --n-estimators 200

O modelo é um RandomForest binário (fraude / não-fraude) por transação,
treinado com um `class_weight` explícito (`{0: 1, 1: fraud_class_weight}`)
para compensar o forte desbalanceamento do PaySim (~0,13% das transações são
fraude). `class_weight="balanced_subsample"` (peso ~1300:1, o inverso exato
da proporção de classes) foi testado primeiro e sub-performou: pesa demais a
classe rara, o que piora a qualidade geral do ranking (PR-AUC). Um peso mais
moderado (`fraud_class_weight=20`, ajustável via `--fraud-class-weight`)
generaliza melhor — ver "Experimentos" em `ml/README.md`.

O split treino/teste é temporal (treina no início da simulação, avalia no
final), não aleatório — mais realista para um modelo que vai rodar em
produção sobre um fluxo de eventos ordenado no tempo.

Esse classificador por transação é o insumo do `score_accounts.py`, que
agrega as probabilidades preditas em um risk score por conta.
"""

import argparse
import json

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)

from ml.config import (
    ARTIFACTS_DIR,
    DEFAULT_DATA_PATH,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
)
from ml.data import load_transactions
from ml.features import FEATURE_COLUMNS, build_transaction_features


def time_based_split(df, test_frac: float):
    cutoff = df["timestamp"].quantile(1 - test_frac)
    train_df = df[df["timestamp"] <= cutoff]
    test_df = df[df["timestamp"] > cutoff]
    return train_df, test_df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA_PATH))
    parser.add_argument("--test-frac", type=float, default=0.2)
    parser.add_argument("--n-estimators", type=int, default=150)
    parser.add_argument("--max-depth", type=int, default=14)
    parser.add_argument(
        "--fraud-class-weight",
        type=float,
        default=20,
        help="Peso da classe fraude relativo à classe não-fraude (peso 1). "
        "Ajustado empiricamente — ver ml/README.md.",
    )
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--model-out", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--metrics-out", default=str(DEFAULT_METRICS_PATH))
    args = parser.parse_args()

    print(f"Carregando transações de {args.data} ...")
    df = load_transactions(args.data)
    print(f"{len(df):,} transações carregadas. Construindo features...")
    df = build_transaction_features(df)

    train_df, test_df = time_based_split(df, args.test_frac)
    print(
        f"Split temporal: {len(train_df):,} treino "
        f"(até {train_df['timestamp'].max()}), "
        f"{len(test_df):,} teste (a partir de {test_df['timestamp'].min()})"
    )

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["isFraud"]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df["isFraud"]

    clf = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        min_samples_leaf=5,
        class_weight={0: 1, 1: args.fraud_class_weight},
        n_jobs=-1,
        random_state=args.random_state,
    )
    print("Treinando RandomForestClassifier...")
    clf.fit(X_train, y_train)

    proba = clf.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, pred, average="binary", zero_division=0
    )

    metrics = {
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
        "fraud_rate_train": float(y_train.mean()),
        "fraud_rate_test": float(y_test.mean()),
        "roc_auc": float(roc_auc_score(y_test, proba)),
        "pr_auc": float(average_precision_score(y_test, proba)),
        "accuracy_at_0.5": float(accuracy_score(y_test, pred)),
        "precision_at_0.5": float(precision),
        "recall_at_0.5": float(recall),
        "f1_at_0.5": float(f1),
        "confusion_matrix_at_0.5": {
            "labels": ["tn", "fp", "fn", "tp"],
            "values": confusion_matrix(y_test, pred).ravel().tolist(),
        },
        "feature_importances": dict(
            sorted(
                zip(FEATURE_COLUMNS, clf.feature_importances_.tolist(), strict=True),
                key=lambda kv: kv[1],
                reverse=True,
            )
        ),
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": clf, "feature_columns": FEATURE_COLUMNS}, args.model_out)
    with open(args.metrics_out, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"\nModelo salvo em {args.model_out}")
    print(f"Métricas salvas em {args.metrics_out}\n")
    print(f"ROC-AUC: {metrics['roc_auc']:.4f}  |  PR-AUC: {metrics['pr_auc']:.4f}")
    print(
        f"Accuracy: {metrics['accuracy_at_0.5']:.4f}  Precision: {precision:.4f}  "
        f"Recall: {recall:.4f}  F1: {f1:.4f}  (limiar 0.5, por transação)"
    )
    print("\nImportância das features:")
    for name, importance in metrics["feature_importances"].items():
        print(f"  {name:<28} {importance:.4f}")


if __name__ == "__main__":
    main()
