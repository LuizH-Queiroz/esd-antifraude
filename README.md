# POC 2 — Antifraude Mínimo Viável

Projeto Final de Engenharia de Sistemas Distribuídos (2026.1) — Documentação Inicial (Projeto 02).

## Sobre o Projeto

Nosso sistema antifraude é um **motor de detecção de fraude** no contexto de sistemas bancários.

O **Sistema Antifraude** analisa transações financeiras em tempo real, calcula um **risk score** baseado nos dados de cada movimentação bancária e aplica **quarentena automática** a contas suspeitas, com revisão humana disponível via Painel Admin.

O sistema é construído como um conjunto de microsserviços, aplicando padrões consagrados de arquitetura distribuída para lidar com escrita/leitura em alto volume, consistência entre serviços e resiliência a falhas.

---

## Arquitetura

### Diagrama C4 — Nível 1 (Contexto)

```mermaid
flowchart TD
    cliente([Cliente Bancário])
    admin([Administrador])
    sistemaBancario[[Sistema Bancário]]
    antifraude[Sistema Antifraude]

    cliente -->|Realiza transações| sistemaBancario
    sistemaBancario -->|Envia eventos de transação| antifraude
    admin -->|Revisa e libera contas| antifraude

    style cliente fill:#ECECEC,stroke:#888,color:#333
    style admin fill:#ECECEC,stroke:#888,color:#333
    style sistemaBancario fill:#F5DCC9,stroke:#D85A30,color:#4A1B0C
    style antifraude fill:#DAD6F5,stroke:#534AB7,color:#26215C
```

O Cliente Bancário não interage diretamente com o Sistema Antifraude — os dados de suas transações, capturados pelo Sistema Bancário, são o principal insumo analisado pelo motor de risk scoring.

### Diagrama C4 — Nível 2 (Containers)

```mermaid
flowchart LR
    admin(["Administrador"])
    sistemaBancario[["Sistema Bancário"]]

    gateway["API Gateway"]
    ingestion["Ingestion Service"]
    scoring["Risk Scoring Service"]
    quarantine["Quarantine Service"]
    adminBackend["Admin Panel Service"]
    broker{{"Message Broker"}}

    sistemaBancario --->|"Eventos de<br>transação"| gateway
    admin --->|"Consultas e<br>comandos (REST)"| gateway

    gateway --->|"Roteia eventos"| ingestion
    gateway --->|"Roteia requisições do Administrador"| adminBackend

    ingestion --->|"Publica<br>TransacaoRegistrada"| broker
    broker --->|"Consome<br>TransacaoRegistrada"| scoring
    scoring --->|"Publica<br>ScoreAltoRisco"| broker
    broker --->|"Consome<br>ScoreAltoRisco"| quarantine
    quarantine --->|"Publica<br>ContaEmQuarentena"| broker
    broker --->|"Consome<br>ContaEmQuarentena"| adminBackend

    adminBackend --->|"Publica<br>ComandoDeLiberaçao"| broker
    broker --->|"Consome<br>ComandoDeLiberaçao"| quarantine
    quarantine --->|"Publica<br>ContaLiberada"| broker
    broker --->|"Consome<br>ContaLiberada"| adminBackend

    style admin fill:#ECECEC,stroke:#888,color:#333
    style sistemaBancario fill:#F5DCC9,stroke:#D85A30,color:#4A1B0C
    style gateway fill:#DAD6F5,stroke:#534AB7,color:#26215C
    style ingestion fill:#CDEBDD,stroke:#0F6E56,color:#04342C
    style scoring fill:#CDEBDD,stroke:#0F6E56,color:#04342C
    style quarantine fill:#CDEBDD,stroke:#0F6E56,color:#04342C
    style adminBackend fill:#CDEBDD,stroke:#0F6E56,color:#04342C
    style broker fill:#F7E2C4,stroke:#854F0B,color:#412402
```

| Microsserviço | Responsabilidade |
|---|---|
| **API Gateway** | Ponto único de entrada para requisições externas e mensagens para fora do sistema |
| **Ingestion Service** | Recebe os eventos brutos de transação vindos do Sistema Bancário (tipo, valor, conta de origem, conta de destino, marco temporal) e os valida/traduz para o modelo de domínio interno antes de publicá-los |
| **Risk Scoring Service** | Consome os eventos de transação e calcula o risk score multifatorial (tipo de transação, valor, padrão temporal, correlação entre contas) |
| **Quarantine Service** | Escuta scores de risco alto, aplica a quarentena automática com base no threshold configurado, e gerencia o ciclo de vida da quarentena (aplicar/liberar) |
| **Admin Panel Service** | Backend que serve o Painel Admin: expõe os casos suspeitos/em quarentena para revisão humana e envia comandos de liberação manual. Contrato de API e relação com o Administrador documentados em [`admin-panel-service/README.md`](admin-panel-service/README.md) |
| **Message Broker** | Infraestrutura de mensageria (não é um microsserviço de negócio, é a peça que viabiliza a comunicação assíncrona entre os serviços acima) |

Cada microsserviço de domínio possui **seu próprio banco de dados** (Database-per-service).

---

## Padrões Arquiteturais Aplicados

| Padrão | Onde se aplica | Motivação |
|---|---|---|
| **Event Sourcing** | Risk Scoring Service, Ingestion Service, Quarantine Service e Admin Panel Service (proposto) | Guarda o histórico completo de eventos (não apenas o estado atual), possibilitando **auditoria total** de como cada score e cada decisão de quarentena foi alcançado — essencial para um sistema antifraude, que precisa justificar decisões perante uma conta que as conteste. No Admin Panel Service, cobre também as ações manuais do Administrador (liberações) — ver [`admin-panel-service/README.md`](admin-panel-service/README.md#padrões-arquiteturais) |
| **CQRS** | Risk Scoring Service, Admin Panel Service (proposto) | Separa o modelo de escrita do modelo de leitura, já que os dois têm padrões de acesso muito diferentes. No Admin Panel Service, o lado de leitura é a projeção de casos de quarentena alimentada por eventos do Quarantine Service — ver [`admin-panel-service/README.md`](admin-panel-service/README.md#padrões-arquiteturais) |
| **SAGA (Choreography)** | Risk Scoring → Quarantine → Admin Panel | Coordena a cadeia "score alto → aplicar quarentena → notificar admin" através de eventos encadeados, sem necessidade de um orquestrador central dado o tamanho reduzido da cadeia |
| **Anti-corruption Layer** | Ingestion Service | Traduz o formato externo de transações do Sistema Bancário (colunas `step`, `type`, `amount`, `nameOrig`, `nameDest`) para o modelo de domínio interno, isolando o sistema de mudanças no contrato externo |

---

## Decisões Arquiteturais (ADRs)

### ADR 001 — Escolha do Message Broker

**Status:** Aceito

**Contexto:** O Sistema Antifraude depende de comunicação assíncrona entre os microsserviços para propagar eventos (`TransacaoRegistrada`, `ScoreAltoRisco`, `ContaEmQuarentena`), seguindo o estilo de **SAGA via Choreography** adotado para coordenar a resposta a um caso de fraude. É necessária uma ferramenta de mensageria com suporte a publish/subscribe, simples de configurar via Docker Compose e sem exigir conhecimento prévio que o grupo não teria tempo hábil de adquirir durante o projeto.

**Decisão:** RabbitMQ como message broker para toda a comunicação assíncrona entre os microsserviços.

**Alternativas consideradas:**
- *Kafka* — mais robusto para altíssimo volume e retenção longa de eventos (o que combinaria bem com Event Sourcing), mas descartado por exigir uma curva de configuração e operação desproporcional ao escopo de uma POC acadêmica.
- *Redis Pub/Sub* — mais leve, porém sem garantia de entrega (mensagens publicadas sem consumidor conectado são perdidas), inaceitável para eventos críticos como a aplicação de quarentena.

**Consequências:**
- Não oferece replay de eventos de longo prazo nativamente como o Kafka — se necessário, isso dependeria do Event Store (PostgreSQL), não do broker.

---

### ADR 002 — Persistência de Dados por Microsserviço

**Status:** Aceito

**Contexto:** Cada microsserviço precisa de armazenamento próprio (Database-per-service). É preciso decidir se cada serviço usa uma tecnologia de banco diferente (polyglot persistence) ou se o grupo padroniza uma única tecnologia. Além disso, o Risk Scoring Service, Ingestion Service e Quarantine Service adotam **Event Sourcing** — o histórico de eventos é a fonte da verdade, não apenas o estado atual — justamente para permitir **auditoria completa** de como cada score e cada decisão de quarentena foram calculados, um requisito importante em qualquer sistema antifraude que precise justificar suas decisões.

**Decisão:** PostgreSQL como banco de dados em todos os microsserviços (leitura e escrita), com uma instância própria por serviço — nunca compartilhada entre eles.

**Alternativas consideradas:**
- *Polyglot persistence completo* (uma tecnologia diferente por serviço) — descartado nesta fase; aumentaria a curva de aprendizado do grupo sem benefício proporcional ao escopo da POC. Fica registrado como possível evolução futura (ex.: Redis como modelo de leitura do CQRS).

**Consequências:**
- Um único conjunto de conhecimento (SQL/PostgreSQL) cobre todo o sistema, facilitando a colaboração entre os desenvolvedores, mesmo trabalhando em diferentes microsserviços.

---

### ADR 003 — Base de Dados para Entrada de Dados

**Status:** Aceito

**Contexto:** Precisamos de dados que sirvam de entrada para o Sistema Antifraude. Esses dados são enviados pelo Sistema Bancário. Sendo assim, a escolha da base de dados é de extrema importância, pois sua escolha definirá o contexto no qual o sistema irá trabalhar, impactando de forma direta e em especial o Risk Scoring Service. A escolha de uma boa base de dados é essencial para que o time possa focar no desenho de uma boa arquitetura e desenvolvimento dos microsserviços, sem se preocupar com problemas relacionados à base de dados, como normalização de dados, filtragem de um número grande de colunas, remoção de linhas duplicadas ou com pouca informação, entre outros.

**Decisão:** Escolhemos a base de dados [Synthetic Financial Datasets For Fraud Detection](https://www.kaggle.com/datasets/ealaxi/paysim1?select=PS_20174392719_1491204439457_log.csv), que é uma base de dados sintética, mas que simula de forma realista transações financeiras, com um grande número de colunas e linhas. A base de dados foi escolhida por ser de fácil acesso, por ser gratuita e por ser de fácil manipulação, além de ter uma relação direta com o contexto do projeto, que é a detecção de fraudes.

Das 11 colunas originais do dataset, apenas 5 são utilizadas como entrada do sistema:
- **step** — marco da hora em que a transação ocorreu;
- **type** — tipo de movimentação bancária (`CASH-IN`, `CASH-OUT`, `DEBIT`, `PAYMENT`, `TRANSFER`);
- **amount** — valor da movimentação;
- **nameOrig** — conta de origem da movimentação;
- **nameDest** — conta de destino da movimentação.

As demais 6 colunas (incluindo `isFraud`, o rótulo original do dataset) não são utilizadas — o simulador da Plataforma Bancária envia apenas os 5 campos acima ao API Gateway, que os repassa ao Ingestion Service.

**Alternativas consideradas:**
- *Base com dados do jogo PUBG* — a base, que se encontra [aqui](https://www.kaggle.com/code/atharvparbalkar/cheater-detection-pubg/input?select=train_V2.csv), apesar de ter muitos dados e colunas com fácil interpretação, foi descartada por não ter uma relação direta com o contexto do projeto. Dessa forma, provavelmente o grupo gastaria um tempo significativo para transformá-la em algo que fosse mais útil ao nosso objetivo e escopo.

**Consequências:**
- Temos uma base de dados que simula de forma realista transações financeiras, com um grande número de linhas e atributos simples, que nos permite focar no desenho de uma boa arquitetura e desenvolvimento dos microsserviços, sem se preocupar com problemas relacionados à base de dados.
- Por usarmos apenas 5 das 11 colunas, o Ingestion Service atua como o ponto que filtra e valida esses campos antes de publicá-los internamente — colunas fora dessas 5 são simplesmente ignoradas na fonte (não retransmitidas pelo simulador), então essa filtragem já ocorre antes de chegar ao Ingestion.
- O fator de risco "correlação entre contas" (do risk score multifatorial) é bem suportado por esse dataset, já que `nameOrig` e `nameDest` permitem identificar cadeias de transferência entre as mesmas contas. Já o fator "device fingerprint", pensado no escopo original do projeto, não tem correspondência nas colunas disponíveis e fica descartado do risk score nesta POC.

---

### ADR 004 — Sistema de Detecção Out-of-Band, sem Autoridade de Bloqueio Síncrono

**Status:** Aceito

**Contexto:** O simulador da Plataforma Bancária faz o replay das linhas do dataset PaySim de forma sequencial e espaçada no tempo, reproduzindo um fluxo contínuo de eventos como se fossem transações acontecendo ao vivo — o que justifica plenamente a arquitetura de microsserviços orientada a eventos (Event Sourcing, CQRS, SAGA), já que o pipeline reage evento a evento, com necessidade real de resiliência e auditabilidade a cada passo.

Isso, no entanto, levanta uma questão separada: o Sistema Antifraude deveria ter autoridade para bloquear uma transação em andamento (exigindo uma chamada síncrona de ida e volta com o Sistema Bancário) ou deveria atuar apenas como um sistema de detecção que sinaliza e quarentena contas com base em padrões observados ao longo do tempo? Essas são duas perguntas independentes — a primeira (fonte de dados via replay) já está resolvida; esta ADR trata da segunda (natureza da resposta do sistema).

O Risk Score depende fortemente do fator "correlação entre contas" (cadeias de transferência via `nameOrig`/`nameDest`), que só é identificável observando **múltiplas transações ao longo do tempo** — não é possível reconhecer esse padrão a partir de uma única transação isolada.

**Decisão:** O Sistema Antifraude opera como um sistema de **detecção out-of-band** (fora do caminho crítico da transação), nunca bloqueando uma transação em andamento nem se comunicando de volta com o Sistema Bancário para aprová-la ou rejeitá-la. A avaliação de risco resulta em dois registros internos, ambos assíncronos e sem retorno ao Sistema Bancário:
- Por **transação**: um rótulo interno (`TransacaoAprovada` / `TransacaoRejeitada`) atribuído após o processamento do evento.
- Por **conta**: quando transações rejeitadas se acumulam em um padrão (não uma ocorrência isolada), a conta é colocada em quarentena — um estado interno do Quarantine Service, consultado pelo Risk Scoring Service para tratar com suspeita elevada quaisquer eventos futuros daquela mesma conta.

**Alternativas consideradas:**
- *Bloqueio síncrono real* (o Sistema Bancário aguarda uma resposta do Antifraude antes de concluir a transação) — descartado. Exigiria uma cadeia de chamadas síncronas e bloqueantes entre API Gateway, Ingestion Service e Risk Scoring Service, contradizendo o desacoplamento pretendido pela SAGA via Choreography já adotada.
- *Bloqueio em tempo real por regra simples de transação* (ex.: valor acima de um limite) — descartado. O Risk Score de é centrado em padrão acumulado entre contas, que só se revela observando uma sequência de transações; um bloqueio isolado por regra simples não tira proveito dos padrões que o sistema foi desenhado para detectar.

**Consequências:**
- Positivas: mantém a arquitetura desacoplada da SAGA via Choreography, sem introduzir um caminho crítico síncrono; alinhado com o funcionamento de sistemas antifraude reais voltados à detecção de padrões acumulados (ex.: lavagem de dinheiro), que tipicamente operam em paralelo ao sistema de pagamentos, não como um gateway de autorização.
- Negativas: o sistema não impede, no momento em que ocorre, que uma transação fraudulenta se complete — a "rejeição" é sempre um registro/rótulo interno pós-processamento, não uma ação real de bloqueio sobre o Sistema Bancário.

---

## Stack Tecnológico

| Camada | Escolha | Justificativa |
|---|---|---|
| **Linguagem / Framework** | Python + FastAPI | Assíncrono nativo, gera documentação OpenAPI automaticamente, curva de aprendizado baixa |
| **Banco de Dados** (leitura e escrita) | PostgreSQL | Ver [ADR 002](#adr-002--persistência-de-dados-por-microsserviço) |
| **Message Broker** | RabbitMQ | Ver [ADR 001](#adr-001--escolha-do-message-broker) |
| **Orquestração local** | Docker Compose | Sobe todos os microsserviços, bancos e broker com um único comando |
| **CI** | GitHub Actions | Lint, testes e build da imagem Docker a cada push/PR, nativo do GitHub |

---

## Ferramentas de IA Utilizadas

| Ferramenta | Onde atuou | Como foi orientada | Avaliação honesta |
|---|---|---|---|
| Claude (Anthropic) | Discussão de arquitetura, diagramas C4, redação de ADRs, montagem dos slides de apresentação | Perguntas incrementais do grupo sobre cada padrão/decisão, com contexto do documento do professor fornecido | Muito útil para aprender o conteúdo, mas é necessário utilizar _esforço de raciocínio_ nível _Médio_ ou maior, além de iterar continuamente, pois normalmente detalhes importantes para o entendimento e motivação dos padrões e diagramas ficam de fora na primeira iteração |
| Claude (Anthropic) | Criação da primeira versão dos slides de apresentação | Dividir os slides em ordem lógica para facilitar a apresentação, além de dividir as partes que cada integrante deveria apresentar e quanto tempo utilizar em cada uma | Também muito útil, pois os slides são organizados, concisos e abordam o conteúdo necessário. Porém, é melhor usá-lo apenas para gerar a primeira versão, pois essa funcionalidade consome muito recurso, reduzindo o crédito para consultas posteriores |
| Claude (Anthropic) | Escrita de documentação | Escrever os documentos, à exemplo deste README, seguindo as diretrizes especificadas, como: ordem das seções, nível de detalhamento, conformidade com o projeto | Muito útil para criar documentação coerente e bem estruturada. Contudo, à medida que as modificações em partes do projeto são decididas e feitas manualmente, fora do escopo da ferramenta, inconsistências vão sendo introduzidas, necessitando constante atualização da ferramenta para mitigar esses erros, além de sempre revisar cuidadosamente o conteúdo gerado |
| GitHub Copilot | Implementação da camada de CI básica | Propor e estruturar um workflow inicial no GitHub Actions para validar o projeto com lint, testes automatizados e build das imagens Docker em cada push/PR | Foi muito boa para acelerar a automação do fluxo de qualidade do repositório, especialmente em um projeto com múltiplos serviços, mas ainda será preciso revisar os passos à medida que o projeto evolui, a fim de garantir que ela esteja funcional para o contexto real do ambiente e do projeto |

---

## Como Executar

```bash
docker compose up --build
```

*(seção a ser completada conforme o setup do ambiente for finalizado pelo grupo)*