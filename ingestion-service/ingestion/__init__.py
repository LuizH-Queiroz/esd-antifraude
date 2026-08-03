"""Ingestion Service do Sistema Antifraude.

Recebe eventos de transação já roteados pelo API Gateway, aplica a
Anti-corruption Layer (traduzindo o formato externo para o modelo de
domínio interno), persiste o evento no Event Store (PostgreSQL) e o
publica no Message Broker (RabbitMQ) para consumo futuro pelo Risk
Scoring Service.

O pacote se chama "ingestion" (não "app", como no simulador, nem
"gateway", como no api-gateway) para evitar colisão de nomes caso, no
futuro, o código de mais de um serviço precise ser importado no mesmo
processo Python (ex.: testes de integração locais, ou o conftest.py da
raiz do repositório, que já adiciona todos os serviços ao sys.path).
"""