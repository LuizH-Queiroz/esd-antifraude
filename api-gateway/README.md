# API Gateway

Ponto único de entrada e saída do Sistema Antifraude. Segundo o README
principal, o Gateway tem dois papéis:

1. **Rotear requisições externas** para os microsserviços responsáveis
   (Sistema Bancário → Ingestion Service);
2. **Rotear as respostas/comandos internos** de volta para fora (Administrador
   ↔ Admin Panel Service).

Nenhum sistema externo fala diretamente com um microsserviço interno — tudo
passa pelo Gateway primeiro.

## Estado atual (Issue #5 — "espinha dorsal")

Nem o Ingestion Service nem o Admin Panel Service existem ainda. O Gateway já
está pronto para rotear para os dois, mas toda chamada vai falhar por conexão
recusada até que cada serviço seja implementado. Isso é esperado nesta fase:
o Gateway trata essa falha explicitamente e devolve **503 Service
Unavailable** — que não é um erro genérico, é a resposta correta ("o serviço
do qual eu dependo está fora do ar"), e é também um dos códigos que o cliente
HTTP do simulador já trata como retryable (ver
`simulator/app/client.py`). Ou seja: assim que o Ingestion Service passar a
existir, o simulador volta a funcionar de ponta a ponta **sem precisar de
nenhuma mudança**.

## Rotas

| Rota | Método | Roteia para | Descrição |
|---|---|---|---|
| `/events/transactions` | `POST` | Ingestion Service | Recebe eventos de transação (hoje, do Simulador) |
| `/admin/{qualquer coisa}` | `GET/POST/PUT/PATCH/DELETE` | Admin Panel Service | Proxy genérico para consultas/comandos do Administrador |
| `/health` | `GET` | — | Liveness: o Gateway está de pé? |
| `/health/dependencies` | `GET` | Ingestion Service, Admin Panel Service | Cada serviço interno está alcançável? |

A rota `/events/transactions` valida a **estrutura** do evento recebido
(campos obrigatórios do envelope — ver `gateway/schemas.py`), mas não faz
validação de regras de negócio: essa responsabilidade é da futura
Anti-corruption Layer do Ingestion Service.

A rota `/admin/**` é um proxy genérico porque o Admin Panel Service ainda não
tem contrato de API definido. Quando ele existir, o esperado é substituir
esse catch-all por rotas específicas, no mesmo estilo de `/events/transactions`.

## Rodando localmente (sem Docker)

```bash
cd api-gateway
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # opcional, ajuste se necessário
uvicorn gateway.main:app --reload --port 8000
```

Depois, com o servidor no ar, é possível testar diretamente (sem o
simulador), por exemplo:

```bash
curl -X POST http://localhost:8000/events/transactions \
  -H "Content-Type: application/json" \
  -d '{
        "event_id": "teste-123",
        "event_type": "TRANSACTION_CREATED",
        "occurred_at": "2026-07-30T12:00:00Z",
        "source": "manual-test",
        "transaction": {
          "step": 1,
          "type": "PAYMENT",
          "amount": 100.0,
          "origin_account": "C1",
          "destination_account": "M1"
        }
      }'
```

Como o Ingestion Service ainda não existe, a resposta esperada é
`503 Service Unavailable` — o que confirma que o Gateway recebeu, validou e
tentou rotear corretamente o evento.

A documentação interativa (Swagger UI), gerada automaticamente pelo FastAPI,
fica disponível em `http://localhost:8000/docs`.

## Rodando via Docker Compose

O `docker-compose.yml` da raiz já declara o serviço `api-gateway` (porta
`8080` no host, mapeada para `8000` no container) com as URLs dos serviços
internos já configuradas como variáveis de ambiente. Diferente dos demais
microsserviços (ainda não implementados, que sobem com
`command: tail -f /dev/null`), o `api-gateway` já sobe executando o servidor
de verdade.

```bash
docker compose up --build api-gateway
```

## Testando a integração simulador → gateway

Com o Gateway no ar (local ou via Docker Compose) e o simulador configurado
para apontar para ele (`API_GATEWAY_URL`, `API_GATEWAY_ENDPOINT` — ver
`simulator/README.md`):

```bash
cd simulator
python main.py --count 5
```

Os logs do Gateway devem mostrar cada evento sendo recebido e a tentativa de
roteamento para o Ingestion Service (com a consequente falha 503, esperada
nesta fase).

## Testes automatizados

Os testes do Gateway ficam em `tests/test_api_gateway.py`, **na raiz do
repositório** (não dentro de `api-gateway/`), porque rodam junto com o
restante da suíte do projeto e usam um `conftest.py` também na raiz para
tornar o pacote `gateway` importável. Rode-os sempre a partir da raiz:

```bash
cd esd-antifraude   # raiz do repositório, não api-gateway/
python3 -m venv .venv        # se ainda não tiver um venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

pytest -q          # roda os testes (do Gateway e de qualquer outro serviço)
ruff check .       # confere o estilo de todo o repositório
```

Não é preciso ter o Docker Compose rodando nem nenhum serviço interno de pé
para esses testes passarem — pelo contrário, eles verificam justamente que
o Gateway se comporta corretamente quando os serviços internos **não**
existem (retornando 503, e não travando ou quebrando). Rode-os depois de
qualquer mudança em `gateway/`, e sempre antes de abrir um PR.

## Estrutura do código
api-gateway/

├── Dockerfile

├── requirements.txt

├── pyproject.toml

├── .env.example

└── gateway/

├── config.py # leitura centralizada de variáveis de ambiente

├── schemas.py # validação estrutural (envelope) dos eventos recebidos

├── proxy.py # encaminhamento HTTP genérico para serviços internos

├── main.py # cria a app FastAPI, gerencia o cliente HTTP compartilhado

└── routes/

├── events.py # POST /events/transactions -> Ingestion Service

├── admin.py # /admin/** -> Admin Panel Service

└── health.py # /health e /health/dependencies

O pacote se chama `gateway` (não `app`, como no simulador) para não colidir
caso, no futuro, o código de mais de um serviço precise ser importado no
mesmo processo Python (ex.: testes de integração locais).