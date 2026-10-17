import os
import json
import base64
import logging
import random
import asyncio
from datetime import datetime, timedelta

import aiosqlite
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# -----------------------------
# Config
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1040670594"))
DB_PATH = os.getenv("DB_PATH", "darikum.db")

# support both names
_webapp_env = os.getenv("WEBAPP_BASE_URL") or os.getenv("WEBAPP_URL") or ""
WEBAPP_BASE_URL = _webapp_env.rstrip("/") if _webapp_env else ""

PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# Helpers
# -----------------------------
def now_iso():
    return datetime.utcnow().isoformat()

def encode_payload(payload: dict) -> str:
    s = json.dumps(payload, ensure_ascii=False)
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def make_webapp_url(payload: dict) -> str:
    if not WEBAPP_BASE_URL:
        return "https://example.com/invalid"
    return f"{WEBAPP_BASE_URL}/?data={encode_payload(payload)}"

def weighted_pick(items):
    if not items:
        return None
    total = sum(int(it.get("weight", 1)) for it in items)
    r = random.randint(1, total)
    acc = 0
    for it in items:
        acc += int(it.get("weight", 1))
        if r <= acc:
            return it.get("label")
    return items[0].get("label")

# -----------------------------
# DB init
# -----------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                image TEXT,
                payload_json TEXT,
                price INTEGER DEFAULT 0,
                created_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS openings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                user_id INTEGER,
                username TEXT,
                result TEXT,
                opened_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                created_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stats (
                user_id INTEGER PRIMARY KEY,
                wins INTEGER DEFAULT 0,
                opens INTEGER DEFAULT 0
            )
            """
        )
        await db.commit()

        # seed example cases if none
        cur = await db.execute("SELECT COUNT(*) FROM cases")
        row = await cur.fetchone()
        cnt = row[0] if row else 0
        if cnt == 0:
            sample = [
                {
                    "title": "Promo Case",
                    "image": "/webapp/assets/case1.png",
                    "price": 0,
                    "items": [
                        {"label": "Sticker Pack", "weight": 50},
                        {"label": "Rare Skin", "weight": 20},
                        {"label": "Epic Box", "weight": 5}
                    ]
                },
                {
                    "title": "Star Case",
                    "image": "/webapp/assets/case2.png",
                    "price": 1,
                    "items": [
                        {"label": "Coins x10", "weight": 60},
                        {"label": "Coins x50", "weight": 25},
                        {"label": "Legendary", "weight": 15}
                    ]
                },
                {
                    "title": "Mystery Case",](#)

            
