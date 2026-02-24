"""
Farallon Capital Agent v5.0 — Точка входа.
Модульная архитектура с 12 улучшениями.
"""
import os
import sys
import asyncio
import logging
from logging.handlers import RotatingFileHandler

# Загрузка .env (если есть)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv не установлен — переменные должны быть в окружении

# ============ LOGGING ============
from config import LOGS_DIR, BOT_VERSION, HEALTHCHECK_PORT, validate_config

logger = logging.getLogger("tg_agent")
logger.setLevel(logging.INFO)

# Файловый хендлер с ротацией
file_handler = RotatingFileHandler(
    os.path.join(LOGS_DIR, "bot.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

# Консольный хендлер
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)


# ============ ЗАГРУЗКА ДАННЫХ ============
from storage import load_all
from prompts import load_ab_tests
from analytics import load_analytics
from market import load_market_cache
from utils import update_healthcheck, auto_backup_loop, healthcheck_loop, start_healthcheck_server
from handlers import client, register_handlers, check_followups
from config import PHONE, TWO_FA_PASSWORD


async def main():
    logger.info(f"{'=' * 50}")
    logger.info(f"Farallon Capital Agent v{BOT_VERSION} — ЗАПУСК")
    logger.info(f"{'=' * 50}")

    # Валидация конфига
    validate_config()

    # Загрузка данных
    load_all()
    load_ab_tests()
    load_analytics()
    load_market_cache()
    update_healthcheck(status="starting")

    logger.info("Все данные загружены")

    # Регистрация обработчиков
    register_handlers()
    logger.info("Обработчики зарегистрированы")

    # Подключение к Telegram
    await client.start(phone=PHONE, password=TWO_FA_PASSWORD)
    me = await client.get_me()
    logger.info(f"Подключён как: {me.first_name} (@{me.username}, ID: {me.id})")

    update_healthcheck(status="running")

    # Запуск фоновых задач
    asyncio.create_task(check_followups())
    asyncio.create_task(auto_backup_loop())
    asyncio.create_task(healthcheck_loop())

    # Запуск healthcheck HTTP сервера
    try:
        await start_healthcheck_server()
    except Exception as e:
        logger.warning(f"Healthcheck HTTP сервер не запущен: {e}")

    logger.info(f"Бот v{BOT_VERSION} полностью запущен и готов к работе!")
    logger.info(f"Healthcheck: http://localhost:{HEALTHCHECK_PORT}/health")

    # Работаем до отключения
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
