"""Consulta do risk score já calculado (incrementalmente) para uma conta.

Equivalente em tempo real ao GET /accounts/{id} de ml/serve.py — mas lendo
de account_stats (atualizado evento a evento, conforme o RabbitMQ entrega
transações), não de um CSV pré-calculado em lote.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["accounts"])


@router.get("/accounts/{account_id}")
async def get_account_risk(account_id: str, request: Request) -> dict:
    store = request.app.state.account_stats_store
    score = await store.get_score(account_id)
    if score is None:
        raise HTTPException(status_code=404, detail="Conta sem transações registradas ainda.")

    return {
        "account_id": score.account_id,
        "risk_score": score.risk_score,
        "tx_count": score.tx_count,
        "high_risk_tx_count": score.high_risk_tx_count,
        "max_p_fraud": score.max_p_fraud,
        "mean_p_fraud": score.mean_p_fraud,
        "total_amount": score.total_amount,
        "last_activity": score.last_activity.isoformat() if score.last_activity else None,
    }