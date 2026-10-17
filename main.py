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
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# -----------------------------
# Config
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1040670594"))
DB_PATH = os.getenv("DB_PATH", "darikum.db")
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# Helpers
# -----------------------------
def now_iso():
    return datetime.utcnow().isoformat()

def weighted_pick(items):
    if not items:
        return None
    total = sum(int(i.get("weight", 1)) for i in items)
    r = random.randint(1, total)
    acc = 0
    for item in items:
        acc += int(item.get("weight", 1))
        if r <= acc:
            return item.get("label")
    return items[0].get("label")

def encode_payload(payload: dict) -> str:
    s = json.dumps(payload, ensure_ascii=False)
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def make_webapp_url(payload: dict) -> str:
    if not WEBAPP_BASE_URL:
        return "https://example.com/invalid"
    return f"{WEBAPP_BASE_URL}/?data={encode_payload(payload)}"

# -----------------------------
# DB init
# -----------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                image TEXT,
                payload_json TEXT,
                price INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS openings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                user_id INTEGER,
                username TEXT,
                result TEXT,
                opened_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id INTEGER PRIMARY KEY,
                wins INTEGER DEFAULT 0,
                opens INTEGER DEFAULT 0
            )
        """)
        await db.commit()

        # seed demo cases if table empty
        cur = await db.execute("SELECT COUNT(*) FROM cases")
        count = (await cur.fetchone())[0]
        if count == 0:
            sample_cases = [
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
                    "title": "Mystery Case",
                    "image": "/webapp/assets/case3.png",
                    "price": 3,
                    "items": [
                        {"label": "Common", "weight": 70},
                        {"label": "Uncommon", "weight": 20},
                        {"label": "Rare", "weight": 10}
                    ]
                }
            ]

            for c in sample_cases:
                await db.execute(
                    "INSERT INTO cases(title, image, payload_json, price, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        c["title"],
                        c["image"],
                        json.dumps({"items": c["items"]}, ensure_ascii=False),
                        c["price"],
                        now_iso(),
                    ),
                )
            await db.commit()
            logger.info("Seeded sample cases")

# -----------------------------
# FastAPI app and webapp static
# -----------------------------
WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")

app = FastAPI()

if os.path.isdir(WEBAPP_DIR):
    app.mount("/webapp", StaticFiles(directory=WEBAPP_DIR), name="webapp")
    app.mount("/", StaticFiles(directory=WEBAPP_DIR), name="root")

@app.get("/api/cases")
async def api_cases():
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute("SELECT id, title, image, payload_json, price FROM cases ORDER BY id").fetchall()

    out = []
    for cid, title, image, payload_json, price in rows:
        payload = json.loads(payload_json) if payload_json else {}
        out.append({
            "id": cid,
            "title": title,
            "image": image,
            "payload": payload,
            "price": price
        })
    return out

@app.get("/api/case/{case_id}")
async def api_case(case_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute("SELECT id, title, image, payload_json, price FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not row:
        return {"error": "not found"}
    cid, title, image, payload_json, price = row
    return {
        "id": cid,
        "title": title,
        "image": image,
        "payload": json.loads(payload_json) if payload_json else {},
        "price": price
    }

# -----------------------------
# Telegram bot
# -----------------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("У вас нет прав администратора.")
            return
        return await func(update, context)
    return wrapper

async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username, points, streak, created_at) VALUES (?, ?, 0, 0, ?)",
            (user.id, user.username or user.full_name, now_iso()),
        )
        await db.execute(
            "INSERT OR IGNORE INTO stats(user_id, wins, opens

