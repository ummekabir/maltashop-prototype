import os, sqlite3
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI

from common import messaging


def open_db(path_env: str, default: str) -> sqlite3.Connection:
    path = os.getenv(path_env, default)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


async def open_channel():
    conn = await messaging.connect()
    channel = await conn.channel(publisher_confirms=True)
    await channel.declare_exchange(messaging.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    return conn, channel
