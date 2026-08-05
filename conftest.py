"""Configuração de coleta de testes compartilhada por todo o repositório.

Cada microsserviço define seu próprio pacote Python (ex.: `api-gateway/gateway/`,
`ingestion-service/ingestion/`, `risk-scoring-service/risk_scoring/`,
`simulator/app/`). Como as pastas têm hífen no nome, elas não são
importáveis diretamente como `import api-gateway...`; em vez disso,
adicionamos cada uma ao `sys.path`, permitindo `import gateway...` /
`import ingestion...` / `import risk_scoring...` de dentro dos testes,
independentemente de onde o `pytest` for executado.

`ml/` não precisa ser adicionada aqui: já é um pacote direto na raiz do
repositório (sem hífen no nome), então `import ml...` já funciona
naturalmente quando o pytest é executado a partir da raiz.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT / "api-gateway"))
sys.path.insert(0, str(_REPO_ROOT / "ingestion-service"))
sys.path.insert(0, str(_REPO_ROOT / "risk-scoring-service"))