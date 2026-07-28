FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# O simulador usa apenas a biblioteca padrão do Python em runtime.
COPY . /app

# Usuário não-root reduz o impacto de uma eventual vulnerabilidade no processo.
RUN useradd --create-home --uid 10001 simulator \
    && mkdir -p /cache \
    && chown -R simulator:simulator /app /cache

USER simulator

CMD ["python", "main.py"]
