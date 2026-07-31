FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copiamos primeiro só o requirements.txt para aproveitar o cache de camadas
# do Docker: o `pip install` só roda de novo se as dependências mudarem.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Usuário não-root reduz o impacto de uma eventual vulnerabilidade no processo.
RUN useradd --create-home --uid 10001 gateway \
    && chown -R gateway:gateway /app

USER gateway

# O docker-compose.yml da raiz ainda sobrescreve este CMD com
# "tail -f /dev/null" para os serviços não implementados — para o
# api-gateway, essa sobrescrita foi removida (ver comentário no
# docker-compose.yml), já que este serviço passa a existir de fato.
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]