"""Orquestra o processamento de um evento TransacaoRegistrada consumido do RabbitMQ."""

from __future__ import annotations

import logging
from typing import Any

from risk_scoring.account_stats import AccountStatsStore
from risk_scoring.features import build_stateless_features, step_to_timestamp
from risk_scoring.model import FraudModel

LOGGER = logging.getLogger(__name__)


async def process_transacao_registrada(
    payload: dict,
    stats_store: AccountStatsStore,
    model: FraudModel,
    high_risk_threshold: float,
    publisher: Any | None = None,
    quarantine_routing_key: str | None = None,
) -> float:
    """Processa um evento já desserializado (dict) publicado pelo Ingestion Service.

    Retorna p_fraud principalmente para facilitar testes/logs — não há
    resposta síncrona a dar a ninguém: este serviço é um consumidor de
    fila, não uma rota HTTP (ver ADR 004 sobre o sistema ser out-of-band).

    Se `publisher` for informado e o score calculado atingir
    `high_risk_threshold`, publica um evento `ScoreAltoRisco` (consumido
    pelo Quarantine Service) além de registrar o resultado normalmente.
    """
    transaction = payload["transaction"]
    step = transaction["step"]
    transaction_type = transaction["type"]
    amount = transaction["amount"]
    origin_account = transaction["origin_account"]
    destination_account = transaction["destination_account"]

    # Timestamp SIMULADO (derivado do step, mesma fórmula do treino) — não
    # o `occurred_at` (horário real de envio) do evento. Usar occurred_at
    # aqui produziria features de padrão temporal com distribuição
    # diferente da que o modelo foi treinado para reconhecer (ver
    # discussão de training/serving skew em features.py).
    event_timestamp = step_to_timestamp(step)

    # 1. Lê o histórico ATUAL (antes desta transação) das duas contas.
    origin_state = await stats_store.get_state(origin_account)
    destination_state = await stats_store.get_state(destination_account)

    # 2. Monta o vetor de features completo: sem estado + causais.
    features = build_stateless_features(step, transaction_type, amount)
    features["orig_prior_tx_count"] = origin_state.tx_count_as_orig
    features["orig_prior_amount_sum"] = origin_state.amount_sum_as_orig
    features["dest_prior_tx_count"] = destination_state.tx_count_as_dest
    features["dest_prior_amount_sum"] = destination_state.amount_sum_as_dest
    features["orig_seen_as_dest_before"] = int(
        origin_state.first_seen_as_dest_at is not None
        and origin_state.first_seen_as_dest_at < event_timestamp
    )
    features["dest_seen_as_orig_before"] = int(
        destination_state.first_seen_as_orig_at is not None
        and destination_state.first_seen_as_orig_at < event_timestamp
    )

    # 3. Roda o modelo.
    p_fraud = model.predict_proba(features)

    # 4. SÓ DEPOIS atualiza o estado — preserva a causalidade (o registro
    # desta transação não pode influenciar as features que ela mesma usou
    # para ser pontuada).
    await stats_store.record_transaction(
        origin_account=origin_account,
        destination_account=destination_account,
        amount=amount,
        p_fraud=p_fraud,
        event_timestamp=event_timestamp,
        high_risk_threshold=high_risk_threshold,
    )

    # 5. Se o score for alto, notifica o Quarantine Service via RabbitMQ.
    if p_fraud >= high_risk_threshold and publisher is not None:
        event_payload = {
            "event_id": payload.get("event_id", f"risk-{origin_account}"),
            "event_type": "ScoreAltoRisco",
            "occurred_at": payload.get("occurred_at", "2026-08-06T00:00:00+00:00"),
            "account_id": origin_account,
            "risk_score": float(p_fraud),
            "motivo": "score acima do threshold configurado",
        }
        await publisher.publish(event_payload)
        LOGGER.info(
            "Evento de risco alto publicado para conta %s com score %.4f.",
            origin_account,
            p_fraud,
        )

    LOGGER.info(
        "Evento %s pontuado: p_fraud=%.4f (origin=%s, destination=%s).",
        payload.get("event_id"), p_fraud, origin_account, destination_account,
    )
    return p_fraud