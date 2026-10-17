# main.py — FastAPI, сервит статику webapp и запускает бота в фоне через on_startup
import os
import asyncio
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

from bot import build_bot_app, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", os.getenv("PORT", "8000")))
WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")

app = FastAPI()

# монтируем статику webapp под / (и /webapp) — в Telegram Web App ссылка на /?data=...
app.mount("/webapp", StaticFiles(directory=WEBAPP_DIR), name="webapp")
app.mount("/", StaticFiles(directory=WEBAPP_DIR), name="root")  # чтобы открыть корень

# флаг, чтобы не стартовать бота дважды при перезагрузках
_bot_task = None
_bot_app = None

@app.on_event("startup")
async def startup_event():
    global _bot_task, _bot_app
    await init_db()
    if _bot_task is None:
        _bot_app = build_bot_app()
        # запускаем бот как фоновую задачу
        _bot_task = asyncio.create_task(_bot_app.run_polling())
        logger.info("Bot background task created.")

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
    # для локального запуска: uvicorn main:app --host 0.0.0.0 --port 8000
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
