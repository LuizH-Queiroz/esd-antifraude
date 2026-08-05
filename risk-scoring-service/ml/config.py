"""Constantes compartilhadas pelo pipeline de risk scoring."""

from datetime import datetime
from pathlib import Path

# As 5 colunas priorizadas conforme a ADR 003 do README raiz do projeto
# (step, type, amount, nameOrig, nameDest) + isFraud, usado apenas como
# rótulo para treino/avaliação offline do modelo (não é um campo que o
# Ingestion Service repassa ao Risk Scoring Service em produção).
RAW_COLUMNS = ["step", "type", "amount", "nameOrig", "nameDest", "isFraud"]

# Categorias fixas de `type` no PaySim. Fixamos a ordem aqui (em vez de
# inferir do dataset em mãos) para que o one-hot encoding gere sempre as
# mesmas colunas, tanto no treino quanto no scoring de um lote novo que,
# por acaso, não contenha alguma das 5 categorias.
TRANSACTION_TYPES = ["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]

# O PaySim não traz um timestamp real: `step` é o número sequencial da hora
# de simulação (1 a 743, ~31 dias). Ancoramos step=1 numa data arbitrária
# apenas para poder derivar features de padrão temporal (hora do dia, dia
# da simulação) a partir de um `timestamp` de verdade, como pedido.
SIMULATION_START = datetime(2023, 1, 1, 0, 0, 0)

ML_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = ML_DIR / "data" / "PS_20174392719_1491204439457_log.csv"
ARTIFACTS_DIR = ML_DIR / "artifacts"
DEFAULT_MODEL_PATH = ARTIFACTS_DIR / "fraud_classifier.joblib"
DEFAULT_METRICS_PATH = ARTIFACTS_DIR / "metrics.json"
DEFAULT_ACCOUNT_SCORES_PATH = ARTIFACTS_DIR / "account_risk_scores.csv"

# Acima desse limiar, uma transação individual é considerada "de alto risco"
# ao contar quantas transações de alto risco cada conta acumulou.
HIGH_RISK_THRESHOLD = 0.5
