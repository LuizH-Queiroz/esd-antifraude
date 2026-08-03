FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN useradd --create-home --uid 10001 ingestion \
    && chown -R ingestion:ingestion /app

USER ingestion

CMD ["uvicorn", "ingestion.main:app", "--host", "0.0.0.0", "--port", "8000"]