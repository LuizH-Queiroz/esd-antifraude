"""Publica um ContaEmQuarentena de exemplo para testar o Admin Panel."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import aio_pika

from admin_panel.config import Settings


async def main() -> None:
    settings = Settings.from_environment()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            settings.exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        payload = {
            "event_id": str(uuid4()),
            "event_type": "ContaEmQuarentena",
            "occurred_at": datetime.now(UTC).isoformat(),
            "account_id": "C90045638",
            "risk_score": 0.87,
            "motivo": "Evento de demonstração publicado manualmente",
        }
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=settings.quarantine_routing_key,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
