"""Configuração de coleta de testes compartilhada por todo o repositório."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT / "api-gateway"))
sys.path.insert(0, str(_REPO_ROOT / "ingestion-service"))
sys.path.insert(0, str(_REPO_ROOT / "admin-panel-service"))
