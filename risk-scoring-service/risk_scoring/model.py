"""Carregamento e inferência do modelo de fraude treinado (ver ml/train.py)."""

from __future__ import annotations

import logging
from pathlib import Path

import joblib
import pandas as pd

LOGGER = logging.getLogger(__name__)


class ModelNotLoadedError(RuntimeError):
    """Levantado se predict_proba() for chamado antes de load()."""


class FraudModel:
    """Encapsula o bundle .joblib (modelo + lista de features) e a inferência.

    A lista de colunas (`feature_columns`) vem do próprio bundle, não é
    hardcoded aqui — mesmo padrão que ml/score_accounts.py já usa
    (`bundle["model"], bundle["feature_columns"]`). Isso garante que, se o
    modelo for retreinado com uma feature a mais/a menos, este serviço se
    adapta automaticamente à ordem certa das colunas, em vez de montar o
    vetor de entrada errado silenciosamente.
    """

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        self._model = None
        self._feature_columns: list[str] | None = None

    def load(self) -> None:
        if not self._model_path.exists():
            raise FileNotFoundError(
                f"Modelo não encontrado em {self._model_path}. Rode "
                "`python -m ml.train` na raiz do repositório e confirme que "
                "o volume 'ml_artifacts' está montado corretamente (ver "
                "docker-compose.yml)."
            )
        bundle = joblib.load(self._model_path)
        self._model = bundle["model"]
        self._feature_columns = bundle["feature_columns"]
        LOGGER.info(
            "Modelo carregado de %s (%s features).",
            self._model_path,
            len(self._feature_columns),
        )

    def predict_proba(self, features: dict) -> float:
        if self._model is None or self._feature_columns is None:
            raise ModelNotLoadedError("load() precisa ser chamado antes de predict_proba().")

        row = pd.DataFrame([features], columns=self._feature_columns)
        return float(self._model.predict_proba(row)[0, 1])