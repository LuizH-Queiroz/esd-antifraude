FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN useradd --create-home --uid 10001 riskscoring \
    && chown -R riskscoring:riskscoring /app

USER riskscoring

CMD ["uvicorn", "risk_scoring.main:app", "--host", "0.0.0.0", "--port", "8000"]