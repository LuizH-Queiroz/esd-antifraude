"""API HTTP de demonstração para servir o modelo de risk scoring treinado.

Duas rotas:

- `POST /predict` — pontua uma transação nova em tempo real (0 a 1, a
  probabilidade de fraude da transação). Para calcular as mesmas features
  causais de correlação entre contas usadas no treino (`orig_prior_*`,
  `dest_prior_*`, `*_seen_as_*_before` — ver `ml/features.py`), mantém um
  **estado em memória por conta** (contagem e soma de transações anteriores
  como remetente/destinatária, atualizado a cada chamada). Esse estado é
  *só desta API de demonstração*, não persiste entre reinícios — em
  produção, o Risk Scoring Service manteria esse histórico no
  `risk-scoring-db` (Event Sourcing, ver ADR 002 do README raiz) em vez de
  em memória, e seria alimentado por eventos `TransacaoRegistrada`
  consumidos do broker, não por chamadas REST síncronas (ver ADR 004: o
  sistema é out-of-band, não bloqueia transações em andamento).
- `GET /accounts/{account_id}` — consulta o risk score da conta já
  pré-calculado em lote por `score_accounts.py`
  (`ml/artifacts/account_risk_scores.csv`), sem precisar rodar o modelo de
  novo.

Rodar (depois de treinar o modelo com `python -m ml.train`):
    uvicorn ml.serve:app --reload --port 8002

Exemplo:
    curl -X POST localhost:8002/predict -H 'Content-Type: application/json' -d '{
      "type": "TRANSFER", "amount": 181000.0,
      "nameOrig": "C123456789", "nameDest": "C987654321"
    }'
"""

from datetime import UTC, datetime
from typing import Literal

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ml.config import (
    DEFAULT_ACCOUNT_SCORES_PATH,
    DEFAULT_MODEL_PATH,
    SIMULATION_START,
)
from ml.features import FEATURE_COLUMNS

app = FastAPI(title="Risk Scoring — API de predição (demo)")

_model_bundle: dict | None = None
_account_scores: dict[str, dict] | None = None

# account_id -> {"orig_tx_count", "orig_amount_sum", "dest_tx_count", "dest_amount_sum"}
_account_state: dict[str, dict] = {}


def _load_model() -> dict:
    global _model_bundle
    if _model_bundle is None:
        if not DEFAULT_MODEL_PATH.exists():
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Modelo não encontrado em {DEFAULT_MODEL_PATH}. "
                    "Rode `python -m ml.train` primeiro.",
                )
            )
        _model_bundle = joblib.load(DEFAULT_MODEL_PATH)
    return _model_bundle


def _load_account_scores() -> dict[str, dict]:
    global _account_scores
    if _account_scores is None:
        if not DEFAULT_ACCOUNT_SCORES_PATH.exists():
            raise HTTPException(
                status_code=503,
                detail=f"Scores não encontrados em {DEFAULT_ACCOUNT_SCORES_PATH}. "
                "Rode `python -m ml.score_accounts` primeiro.",
            )
        df = pd.read_csv(DEFAULT_ACCOUNT_SCORES_PATH)
        _account_scores = df.set_index("account_id").to_dict(orient="index")
    return _account_scores


class TransactionIn(BaseModel):
    type: Literal["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]
    amount: float = Field(gt=0)
    nameOrig: str
    nameDest: str
    # Opcional: se omitido, usa o instante da chamada. No PaySim real isso
    # viria da conversão de `step` (ver ml/data.py); aqui, numa transação ao
    # vivo, já é um timestamp de verdade.
    timestamp: datetime | None = None


class ScoreOut(BaseModel):
    p_fraud: float
    risk_level: Literal["baixo", "medio", "alto"]
    orig_prior_tx_count: int
    dest_prior_tx_count: int


def _risk_level(p_fraud: float) -> str:
    if p_fraud >= 0.7:
        return "alto"
    if p_fraud >= 0.3:
        return "medio"
    return "baixo"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict", response_model=ScoreOut)
def predict(tx: TransactionIn):
    bundle = _load_model()
    model = bundle["model"]

    timestamp = tx.timestamp or datetime.now(UTC)

    # Lê o estado ANTES de atualizá-lo — as features precisam refletir só o
    # histórico anterior a esta transação, igual ao treino (causal).
    orig_state = _account_state.get(tx.nameOrig, {})
    dest_state = _account_state.get(tx.nameDest, {})

    row = dict.fromkeys(FEATURE_COLUMNS, 0)
    row["amount"] = tx.amount
    row["amount_log"] = float(np.log1p(tx.amount))
    row["hour_of_day"] = timestamp.hour
    row["day_index"] = max((timestamp.replace(tzinfo=None) - SIMULATION_START).days, 0)
    row["orig_prior_tx_count"] = orig_state.get("orig_tx_count", 0)
    row["orig_prior_amount_sum"] = orig_state.get("orig_amount_sum", 0.0)
    row["dest_prior_tx_count"] = dest_state.get("dest_tx_count", 0)
    row["dest_prior_amount_sum"] = dest_state.get("dest_amount_sum", 0.0)
    row["orig_seen_as_dest_before"] = int(orig_state.get("dest_tx_count", 0) > 0)
    row["dest_seen_as_orig_before"] = int(dest_state.get("orig_tx_count", 0) > 0)
    row[f"type_{tx.type}"] = 1

    X = pd.DataFrame([row], columns=FEATURE_COLUMNS)
    p_fraud = float(model.predict_proba(X)[0, 1])

    orig = _account_state.setdefault(tx.nameOrig, {})
    orig["orig_tx_count"] = orig.get("orig_tx_count", 0) + 1
    orig["orig_amount_sum"] = orig.get("orig_amount_sum", 0.0) + tx.amount

    dest = _account_state.setdefault(tx.nameDest, {})
    dest["dest_tx_count"] = dest.get("dest_tx_count", 0) + 1
    dest["dest_amount_sum"] = dest.get("dest_amount_sum", 0.0) + tx.amount

    return ScoreOut(
        p_fraud=round(p_fraud, 4),
        risk_level=_risk_level(p_fraud),
        orig_prior_tx_count=row["orig_prior_tx_count"],
        dest_prior_tx_count=row["dest_prior_tx_count"],
    )


@app.get("/accounts/{account_id}")
def get_account_risk(account_id: str):
    scores = _load_account_scores()
    if account_id not in scores:
        raise HTTPException(
            status_code=404, detail="Conta não encontrada nos scores pré-calculados"
        )
    return {"account_id": account_id, **scores[account_id]}
