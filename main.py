import os
import json
import base64
import logging
import random
import asyncio
from datetime import datetime

import aiosqlite
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# -----------------------------
# Config
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1040670594"))
DB_PATH = os.getenv("DB_PATH", "darikum.db")

# Support both names: WEBAPP_BASE_URL or WEBAPP_URL
_webapp_env = os.getenv("WEBAPP_BASE_URL") or os.getenv("WEBAPP_URL") or ""
WEBAPP_BASE_URL = _webapp_env.rstrip("/") if _webapp_env else ""

PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

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
# Database initialization
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

        # Seed sample cases if empty
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
            for c in sample:
                await db.execute(
                    "INSERT INTO cases(title, image, payload_json, price, created_at) VALUES (?, ?, ?, ?, ?)",
                    (c["title"], c["image"], json.dumps({"items": c["items"]}, ensure_ascii=False), c["price"], now_iso())
                )
            await db.commit()
            logger.info("Seeded sample cases")

# -----------------------------
# FastAPI app & static
# -----------------------------
WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")
app = FastAPI()

if os.path.isdir(WEBAPP_DIR):
    app.mount("/webapp", StaticFiles(directory=WEBAPP_DIR), name="webapp")
    app.mount("/", StaticFiles(directory=WEBAPP_DIR), name="root")

# Health/debug endpoints to help diagnose deployment
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/debug-files", include_in_schema=False)
async def debug_files():
    import os
    webdir = WEBAPP_DIR
    exists = os.path.isdir(webdir)
    try:
        listing = os.listdir(webdir)[:100] if exists else []
    except Exception as e:
        listing = [str(e)]
    return JSONResponse({"WEBAPP_BASE_URL": WEBAPP_BASE_URL, "webapp_dir_exists": exists, "webapp_files_sample": listing})

# API endpoints
@app.get("/api/cases")
async def api_cases():
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute("SELECT id, title, image, payload_json, price FROM cases ORDER BY id").fetchall()
    out = []
    for cid, title, image, payload_json, price in rows:
        payload = json.loads(payload_json) if payload_json else {}
        out.append({"id": cid, "title": title, "image": image, "payload": payload, "price": price})
    return out

@app.get("/api/case/{case_id}")
async def api_case(case_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute(
            "SELECT id, title, image, payload_json, price FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    cid, title, image, payload_json, price = row
    return {"id": cid, "title": title, "image": image, "payload": json.loads(payload_json) if payload_json else {}, "price": price}

# -----------------------------
# Telegram bot
# -----------------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            # silent return for non-admins
            if update.message:
                await update.message.reply_text("У вас нет прав администратора.")
            return
        return await func(update, context)
    return wrapper

async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username, points, streak, created_at) VALUES (?, ?, 0, 0, ?)",
            (user.id, user.username or user.full_name, now_iso())
        )
        await db.execute(
            "INSERT OR IGNORE INTO stats(user_id, wins, opens) VALUES (?, 0, 0)",
            (user.id,)
        )
        await db.commit()

def build_bot_app():
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN missing; bot will not start.")
        return None

    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await ensure_user(user)
        weburl = make_webapp_url({"mode": "shop"})
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть магазин кейсов", web_app=WebAppInfo(weburl))]])
        if update.message:
            await update.message.reply_text("Привет! Открой магазин кейсов:", reply_markup=kb)

    @admin_only
    async def cmd_add_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = " ".join(context.args) if context.args else ""
        if not raw:
            await update.message.reply_text("Использование: /add_case Title|price|label:weight,label:weight,...|image")
            return
        try:
            title, price_s, items_raw, *rest = [p.strip() for p in raw.split("|")]
            price = int(price_s)
            image = rest[0] if rest else "/webapp/assets/case1.png"
            items = []
            for it in items_raw.split(","):
                lbl, wt = [x.strip() for x in it.split(":", 1)]
                items.append({"label": lbl, "weight": int(wt)})
        except Exception:
            await update.message.reply_text("Ошибка формата. Пример:\n/add_case Promo|1|Sticker:80,Box:20|/webapp/assets/case1.png")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO cases(title, image, payload_json, price, created_at) VALUES (?, ?, ?, ?, ?)",
                (title, image, json.dumps({"items": items}, ensure_ascii=False), price, now_iso())
            )
            await db.commit()
        await update.message.reply_text("Кейс добавлен.")

    async def handle_webapp_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not getattr(msg, "web_app_data", None):
            return
        try:
            data = json.loads(msg.web_app_data.data)
        except Exception:
            await msg.reply_text("Ошибка данных из мини‑приложения.")
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

                await db.execute(
                    "INSERT INTO openings(case_id, user_id, username, result, opened_at) VALUES (?, ?, ?, ?, ?)",
                    (case_id, user.id, user.username or user.full_name, result, now_iso())
                )
                await db.execute(
                    "INSERT OR IGNORE INTO users(user_id, username, points, streak, created_at) VALUES (?, ?, 0, 0, ?)",
                    (user.id, user.username or user.full_name, now_iso())
                )
                await db.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (user.id,))
                await db.execute("INSERT OR IGNORE INTO stats(user_id, wins, opens) VALUES (?, 0, 0)", (user.id,))
                await db.execute("UPDATE stats SET opens = opens + 1 WHERE user_id = ?", (user.id,))
                await db.commit()

            await msg.reply_text(f"🎉 Вы открыли кейс и получили: {result}")
            try:
                await context.bot.send_message(msg.chat_id, f"🎉 @{user.username or user.full_name} открыл кейс #{case_id} и получил: {result}")
            except Exception:
                logger.exception("Failed to announce result to chat")
            return

        await msg.reply_text("Неизвестное действие из мини‑приложения.")

    app_bot.add_handler(CommandHandler("start", cmd_start))
    app_bot.add_handler(CommandHandler("add_case", cmd_add_case))
    app_bot.add_handler(MessageHandler(filters.ALL, handle_webapp_message))
    return app_bot

# -----------------------------
# Startup / shutdown with webhook removal & robust polling
# -----------------------------
_bot_app = None
_bot_polling_task = None

@app.on_event("startup")
async def startup_event():
    global _bot_app, _bot_polling_task
    await init_db()

    logger.info("WEBAPP_BASE_URL = %s", WEBAPP_BASE_URL)

    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set; bot will not start.")
        return

    _bot_app = build_bot_app()
    if not _bot_app:
        return

    try:
        logger.info("Initializing telegram application...")
        await _bot_app.initialize()
        logger.info("Starting telegram application...")
        await _bot_app.start()

        # Remove webhook if set — safe step before polling
        try:
            logger.info("Deleting webhook (if any) before starting polling...")
            await _bot_app.bot.delete_webhook(drop_pending_updates=True)
            logger.info("delete_webhook() completed")
        except Exception as e:
            logger.warning("delete_webhook() failed or not necessary: %s", e)

        # Start polling as background task (PTB 20.x)
        if getattr(_bot_app, "updater", None) is not None and hasattr(_bot_app.updater, "start_polling"):
            try:
                _bot_polling_task = asyncio.create_task(_bot_app.updater.start_polling())
                logger.info("Polling started in background task.")
            except Exception as e:
                logger.exception("Failed to start polling background task: %s", e)
        else:
            logger.warning("Updater or start_polling not available; polling not started")
    except Exception:
        logger.exception("Failed to start bot in startup_event, attempting shutdown")
        try:
            await _bot_app.shutdown()
        except Exception:
            pass

@app.on_event("shutdown")
async def shutdown_event():
    global _bot_app, _bot_polling_task
    if _bot_app is None:
        return

    logger.info("Shutting down Telegram bot...")

    try:
        if getattr(_bot_app, "updater", None) is not None and hasattr(_bot_app.updater, "stop_polling"):
            try:
                await _bot_app.updater.stop_polling()
            except Exception:
                logger.exception("Error stopping updater polling")
    except Exception:
        logger.exception("Error during stopping polling")

    if _bot_polling_task is not None:
        _bot_polling_task.cancel()
        try:
            await _bot_polling_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error awaiting _bot_polling_task")

    try:
        await _bot_app.stop()
    except Exception:
        logger.exception("Error on _bot_app.stop()")

    try:
        await _bot_app.shutdown()
    except Exception:
        logger.exception("Error on _bot_app.shutdown()")

    _bot_app = None
    _bot_polling_task = None

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
