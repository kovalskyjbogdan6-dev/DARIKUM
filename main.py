# main.py
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

# config
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "darikum.db")
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def now_iso():
    return datetime.utcnow().isoformat()

def encode_payload(p: dict) -> str:
    return base64.b64encode(json.dumps(p, ensure_ascii=False).encode()).decode()

def make_webapp_url(payload: dict) -> str:
    if not WEBAPP_BASE_URL:
        return "https://example.com/invalid"
    return f"{WEBAPP_BASE_URL}/?data={encode_payload(payload)}"

def weighted_pick(items):
    if not items:
        return None
    total = sum(int(i.get("weight", 1)) for i in items)
    r = random.randint(1, total)
    acc = 0
    for i in items:
        acc += int(i.get("weight", 1))
        if r <= acc:
            return i.get("label")
    return items[0].get("label")

# DB
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
        await db.commit()

        # seed sample cases if empty
        cur = await db.execute("SELECT COUNT(*) FROM cases")
        cnt = (await cur.fetchone())[0]
        if cnt == 0:
            sample = [
                {
                    "title": "Promo Case",
                    "image": "/webapp/assets/case1.png",
                    "price": 0,
                    "items": [{"label":"Sticker Pack","weight":50},{"label":"Rare Skin","weight":20},{"label":"Epic Box","weight":5}]
                },
                {
                    "title": "Star Case",
                    "image": "/webapp/assets/case2.png",
                    "price": 1,
                    "items": [{"label":"Coin x10","weight":60},{"label":"Coin x50","weight":30},{"label":"Legendary","weight":10}]
                },
                {
                    "title": "Mystery Case",
                    "image": "/webapp/assets/case3.png",
                    "price": 3,
                    "items": [{"label":"Common","weight":70},{"label":"Uncommon","weight":20},{"label":"Rare","weight":10}]
                }
            ]
            for c in sample:
                await db.execute(
                    "INSERT INTO cases(title,image,payload_json,price,created_at) VALUES (?, ?, ?, ?, ?)",
                    (c["title"], c["image"], json.dumps({"items": c["items"]}, ensure_ascii=False), c["price"], now_iso())
                )
            await db.commit()
            logger.info("Seeded sample cases")

# FastAPI & static
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
    for r in rows:
        cid, title, image, payload_json, price = r
        payload = json.loads(payload_json) if payload_json else {}
        out.append({"id": cid, "title": title, "image": image, "payload": payload, "price": price})
    return out

@app.get("/api/case/{case_id}")
async def api_case(case_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute("SELECT id, title, image, payload_json, price FROM cases WHERE id=?", (case_id,)).fetchone()
    if not row:
        return {"error":"not found"}
    cid, title, image, payload_json, price = row
    return {"id": cid, "title": title, "image": image, "payload": json.loads(payload_json), "price": price}

# Bot
_bot_task = None
_bot_app = None

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("У вас нет прав администратора.")
            return
        return await func(update, context)
    return wrapper

def build_bot_app():
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set; bot won't start")
        return None
    bot = ApplicationBuilder().token(BOT_TOKEN).build()

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        weburl = make_webapp_url({"mode":"shop"})
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть магазин кейсов", web_app=WebAppInfo(weburl))]])
        await update.message.reply_text("Привет! Открывай магазин кейсов:", reply_markup=kb)

    @admin_only
    async def cmd_add_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # simple parser: /add_case Title|price|label:weight,label:weight,...|image_path(optional)
        raw = " ".join(context.args) if context.args else ""
        if not raw:
            await update.message.reply_text("Использование: /add_case Title|price|label:weight,...|/webapp/assets/img.png")
            return
        try:
            title, price_s, items_raw, *rest = [p.strip() for p in raw.split("|")]
            price = int(price_s)
            image = rest[0] if rest else "/webapp/assets/case1.png"
            items = []
            for it in items_raw.split(","):
                lbl, wt = [x.strip() for x in it.split(":",1)]
                items.append({"label":lbl,"weight":int(wt)})
        except Exception:
            await update.message.reply_text("Ошибка формата.")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO cases(title,image,payload_json,price,created_at) VALUES (?, ?, ?, ?, ?)",
                             (title, image, json.dumps({"items":items}, ensure_ascii=False), price, now_iso()))
            await db.commit()
        await update.message.reply_text("Кейс добавлен.")

    async def handle_webapp_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not getattr(msg, "web_app_data", None):
            return
        try:
            data = json.loads(msg.web_app_data.data)
        except Exception:
            await msg.reply_text("Некорректные данные из мини‑приложения.")
            return
        action = data.get("action")
        case_id = int(data.get("case_id")) if data.get("case_id") else None
        user = update.effective_user

        if action == "open_case" and case_id:
            async with aiosqlite.connect(DB_PATH) as db:
                row = await db.execute("SELECT payload_json FROM cases WHERE id=?", (case_id,)).fetchone()
                if not row:
                    await msg.reply_text("Кейс не найден.")
                    return
                payload = json.loads(row[0]) if row[0] else {}
                items = payload.get("items", [])
                result = weighted_pick(items) or "Пусто"
                await db.execute("INSERT INTO openings(case_id,user_id,username,result,opened_at) VALUES (?, ?, ?, ?, ?)",
                                 (case_id, user.id, user.username or user.full_name, result, now_iso()))
                await db.commit()
            # Reply inside webapp and in chat
            await msg.reply_text(f"🎉 Вы открыли кейс и получили: {result}")
            try:
                await context.bot.send_message(msg.chat_id, f"🎉 @{user.username or user.full_name} открыл(а) кейс #{case_id} и получил(а): {result}")
            except Exception:
                pass
            return

        await msg.reply_text("Неизвестное действие мини‑приложения.")

    bot.add_handler(CommandHandler("start", cmd_start))
    bot.add_handler(CommandHandler("add_case", cmd_add_case))
    bot.add_handler(MessageHandler(filters.ALL, handle_webapp_message))
    return bot

# startup / shutdown
@app.on_event("startup")
async def startup_event():
    await init_db()
    global _bot_task, _bot_app
    if BOT_TOKEN:
        _bot_app = build_bot_app()
        if _bot_app:
            logger.info("Launching bot background task")
            _bot_task = asyncio.create_task(_bot_app.run_polling())

@app.on_event("shutdown")
async def shutdown_event():
    global _bot_task, _bot_app
    if _bot_app:
        await _bot_app.stop()
    if _bot_task:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
