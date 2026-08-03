"""Configuração de coleta de testes compartilhada por todo o repositório.

Cada microsserviço define seu próprio pacote Python (ex.: `api-gateway/gateway/`,
`ingestion-service/ingestion/`, `simulator/app/`). Como as pastas têm hífen
no nome (`api-gateway`, `ingestion-service`), elas não são importáveis
diretamente como `import api-gateway...`; em vez disso, adicionamos a
própria pasta ao `sys.path`, permitindo `import gateway...` / `import
ingestion...` de dentro dos testes, independentemente de onde o `pytest`
for executado.

Isso é o que permite ao `pytest -q` rodado na raiz do projeto (ver
.github/workflows/ci-basic.yml) encontrar e importar o código de cada
serviço para testá-lo.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT / "api-gateway"))
sys.path.insert(0, str(_REPO_ROOT / "ingestion-service"))