"""Conversão do formato PaySim para a mensagem externa do simulador.

Manter esta transformação em um módulo isolado é importante porque o contrato
definitivo do API Gateway ainda não existe. Quando o endpoint for implementado,
a maior parte de uma eventual adaptação ficará concentrada aqui.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.models import PaySimTransaction


class TransactionMessageMapper:
    """Cria eventos independentes do formato físico do CSV."""

    def __init__(
        self,
        *,
        selection_strategy: str = "sequential",
        include_balance_fields: bool = False,
        include_ground_truth: bool = False,
        uuid_factory: Callable[[], UUID] = uuid4,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        # Reporta a estratégia de fato usada (sequencial ou aleatória com
        # reposição) em vez de um valor fixo, já que agora ambas coexistem
        # (ver SAMPLING_STRATEGY em app/config.py e a discussão na Issue #5).
        self.selection_strategy = selection_strategy
        self.include_balance_fields = include_balance_fields
        self.include_ground_truth = include_ground_truth
        self._uuid_factory = uuid_factory
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    def to_message(self, transaction: PaySimTransaction) -> dict[str, Any]:
        """Converte uma transação em uma mensagem JSON pronta para envio."""
        event_id = str(self._uuid_factory())
        occurred_at = self._now_factory().astimezone(UTC)

        message: dict[str, Any] = {
            "event_id": event_id,
            "event_type": "TRANSACTION_CREATED",
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "source": "paysim-simulator",
            "transaction": {
                "step": transaction.step,
                "type": transaction.transaction_type,
                "amount": transaction.amount,
                "origin_account": transaction.origin_account,
                "destination_account": transaction.destination_account,
            },
            "simulation_metadata": {
                "dataset": "PaySim",
                "selection": self.selection_strategy,
            },
        }

        # A própria documentação da PaySim alerta que os saldos não devem ser
        # utilizados como atributos de detecção, pois transações fraudulentas são
        # canceladas na simulação. Eles permanecem opcionais para inspeções e
        # testes de integração, mas ficam fora do fluxo operacional por padrão.
        if self.include_balance_fields:
            message["transaction"].update(
                {
                    "origin_balance_before": transaction.origin_balance_before,
                    "origin_balance_after": transaction.origin_balance_after,
                    "destination_balance_before": transaction.destination_balance_before,
                    "destination_balance_after": transaction.destination_balance_after,
                }
            )

        # Os rótulos revelam a resposta correta do dataset. Por padrão, eles não
        # seguem para o sistema antifraude para evitar que o futuro algoritmo de
        # scoring "descubra" a fraude lendo diretamente o gabarito. Eles podem
        # ser habilitados em testes comparativos com INCLUDE_GROUND_TRUTH=true.
        if self.include_ground_truth:
            message["simulation_metadata"]["ground_truth"] = {
                "is_fraud": transaction.is_fraud,
                "is_flagged_fraud": transaction.is_flagged_fraud,
            }

        return message