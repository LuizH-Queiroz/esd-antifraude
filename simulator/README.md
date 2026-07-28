# Simulador PaySim

Este componente representa o sistema externo que produz transações para o
Sistema Antifraude. A cada ciclo ele sorteia **uma linha aleatória** da PaySim,
converte a linha em uma mensagem JSON e a envia por HTTP ao API Gateway.

## Por que existe um índice?

A PaySim possui milhões de linhas. O simulador não carrega tudo em memória nem
percorre o CSV sequencialmente para enviar registros. Na primeira execução ele
faz uma única passagem para criar um índice de offsets. Depois disso, cada envio:

1. sorteia uniformemente um número de linha;
2. consulta a posição dessa linha no índice;
3. salta diretamente para ela no CSV;
4. monta e envia a mensagem.

O sorteio é com reposição, portanto a mesma transação pode aparecer novamente.

## Preparação da base

Baixe o arquivo pela página da PaySim no Kaggle e coloque-o em:

```text
data/PS_20174392719_1491204439457_log.csv
```

Não renomeie nem versione o CSV. Mais detalhes estão em `data/README.md`.

## Teste local sem API Gateway

Na pasta `simulator`:

```bash
python main.py --once --dry-run
```

O comando cria/reutiliza o índice, sorteia uma linha aleatória e imprime o JSON.
Para imprimir cinco sorteios:

```bash
python main.py --count 5 --dry-run
```

## Teste local enviando para um Gateway

Em Linux/macOS:

```bash
export API_GATEWAY_URL=http://localhost:8080
export API_GATEWAY_ENDPOINT=/events/transactions
python main.py --count 10
```

Em PowerShell:

```powershell
$env:API_GATEWAY_URL = "http://localhost:8080"
$env:API_GATEWAY_ENDPOINT = "/events/transactions"
python main.py --count 10
```

O endpoint é configurável porque o API Gateway ainda não foi implementado. A
transformação do contrato fica isolada em `app/mapper.py`.

## Execução pelo Docker Compose

Depois de aplicar o trecho indicado em `root-changes/docker-compose.md`, execute
na raiz do repositório:

```bash
docker compose up --build simulator
```

Como o Gateway ainda não existe, o primeiro teste recomendado é:

```bash
docker compose run --rm simulator python main.py --once --dry-run
```

## Formato enviado

Exemplo resumido:

```json
{
  "event_id": "uuid",
  "event_type": "TRANSACTION_CREATED",
  "occurred_at": "2026-07-24T18:00:00Z",
  "source": "paysim-simulator",
  "transaction": {
    "step": 1,
    "type": "PAYMENT",
    "amount": 1060.31,
    "origin_account": "C429214117",
    "destination_account": "M1591654462"
  },
  "simulation_metadata": {
    "dataset": "PaySim",
    "selection": "random_with_replacement"
  }
}
```

Os quatro campos de saldo também ficam fora por padrão, porque a documentação
da PaySim alerta que eles não devem ser usados na detecção de fraude. Para um
teste de integração que precise reproduzir a linha completa, use
`INCLUDE_BALANCE_FIELDS=true`.

`isFraud` e `isFlaggedFraud` são o gabarito da base e ficam fora do evento por
padrão, evitando vazamento de resposta para o futuro risk-scoring-service. Para
um teste comparativo controlado, use `INCLUDE_GROUND_TRUTH=true`.