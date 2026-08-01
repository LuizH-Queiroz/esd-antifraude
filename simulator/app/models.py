"""Modelos internos usados para representar uma linha da PaySim."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PAYMENT_TYPES = {"CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"}


class InvalidDatasetRow(ValueError):
    """Indica que uma linha do CSV não respeita o formato esperado."""


@dataclass(frozen=True, slots=True)
class PaySimTransaction:
    """Representação tipada de uma transação da base PaySim.

    Os nomes Python usam snake_case, mas o método ``from_csv_row`` conhece os
    nomes originais do CSV. Isso evita espalhar detalhes da base de dados pelo
    restante do simulador.
    """

    step: int
    transaction_type: str
    amount: float
    origin_account: str
    origin_balance_before: float
    origin_balance_after: float
    destination_account: str
    destination_balance_before: float
    destination_balance_after: float
    is_fraud: bool
    is_flagged_fraud: bool

    @classmethod
    def from_csv_row(cls, row: Mapping[str, str]) -> PaySimTransaction:
        try:
            transaction_type = row["type"].strip().upper()
            if transaction_type not in PAYMENT_TYPES:
                raise InvalidDatasetRow(
                    f"Tipo de transação desconhecido: {transaction_type!r}."
                )

            return cls(
                step=int(row["step"]),
                transaction_type=transaction_type,
                amount=float(row["amount"]),
                origin_account=row["nameOrig"].strip(),
                origin_balance_before=float(row["oldbalanceOrg"]),
                origin_balance_after=float(row["newbalanceOrig"]),
                destination_account=row["nameDest"].strip(),
                destination_balance_before=float(row["oldbalanceDest"]),
                destination_balance_after=float(row["newbalanceDest"]),
                is_fraud=_parse_binary_flag(row["isFraud"], "isFraud"),
                is_flagged_fraud=_parse_binary_flag(
                    row["isFlaggedFraud"], "isFlaggedFraud"
                ),
            )
        except KeyError as exc:
            raise InvalidDatasetRow(f"Coluna ausente no CSV: {exc.args[0]}.") from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, InvalidDatasetRow):
                raise
            raise InvalidDatasetRow(f"Linha PaySim inválida: {dict(row)!r}.") from exc


def _parse_binary_flag(value: str, column_name: str) -> bool:
    normalized = value.strip()
    if normalized == "0":
        return False
    if normalized == "1":
        return True
    raise InvalidDatasetRow(
        f"A coluna {column_name} deve conter 0 ou 1; valor recebido: {value!r}."
    )