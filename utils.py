"""
Утилиты — время, healthcheck, auto-backup, scheduling.
"""
import os
import json
import glob
import shutil
import asyncio
import logging
import time
from datetime import datetime, timedelta
from config import (
    TIMEZONE_OFFSET, NIGHT_START_HOUR, NIGHT_END_HOUR,
    BACKUP_DIR, DATA_DIR, BACKUP_INTERVAL, BACKUP_MAX_FILES,
    HEALTHCHECK_FILE, HEALTHCHECK_INTERVAL, HEALTHCHECK_PORT,
    BOT_VERSION,
)

logger = logging.getLogger("tg_agent")


# ============ ВРЕМЯ (MSK) ============
def get_moscow_hour() -> int:
    utc_now = datetime.utcnow()
    moscow_now = utc_now + timedelta(hours=TIMEZONE_OFFSET)
    return moscow_now.hour

def get_moscow_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)

def is_night_time() -> bool:
    """Проверяет, ночное ли время (MSK) — не отправляем сообщения"""
    hour = get_moscow_hour()
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR

def get_time_greeting() -> str:
    hour = get_moscow_hour()
    if 6 <= hour < 12:
        return "Доброе утро"
    elif 12 <= hour < 18:
        return "Добрый день"
    elif 18 <= hour < 23:
        return "Добрый вечер"
    else:
        return "Привет"


# ============ TIMEZONE-AWARE SCHEDULING ============
def is_appropriate_time_for_user(country: str = None) -> bool:
    """
    Проверяет, подходящее ли время для отправки сообщения.
    Учитывает часовой пояс клиента (если известна страна).
    """
    # Базовая проверка по MSK
    if is_night_time():
        return False

    # Если страна известна — проверяем по её часовому поясу
    if country:
        user_offset = _get_timezone_offset(country)
        if user_offset is not None:
            utc_now = datetime.utcnow()
            user_hour = (utc_now + timedelta(hours=user_offset)).hour
            # Не отправляем с 23:00 до 7:00 по времени клиента
            if user_hour >= 23 or user_hour < 7:
                logger.info(f"Ночное время у клиента ({country}, {user_hour}:00), откладываем")
                return False
    return True

def _get_timezone_offset(country: str) -> int | None:
    """Возвращает примерный UTC offset для страны"""
    country_lower = country.lower().strip()
    tz_map = {
        # СНГ
        "россия": 3, "москва": 3, "russia": 3, "moscow": 3,
        "украина": 2, "киев": 2, "ukraine": 2, "kyiv": 2, "kiev": 2,
        "беларусь": 3, "минск": 3, "belarus": 3,
        "казахстан": 6, "алматы": 6, "kazakhstan": 6,
        "узбекистан": 5, "ташкент": 5, "uzbekistan": 5,
        "грузия": 4, "тбилиси": 4, "georgia": 4,
        "азербайджан": 4, "баку": 4, "azerbaijan": 4,
        "армения": 4, "ереван": 4, "armenia": 4,
        "молдова": 2, "кишинёв": 2, "moldova": 2,
        # Европа
        "германия": 1, "берлин": 1, "germany": 1,
        "франция": 1, "париж": 1, "france": 1,
        "испания": 1, "мадрид": 1, "spain": 1,
        "италия": 1, "рим": 1, "italy": 1,
        "великобритания": 0, "лондон": 0, "uk": 0, "england": 0,
        "польша": 1, "варшава": 1, "poland": 1,
        "чехия": 1, "прага": 1, "czech": 1,
        "нидерланды": 1, "амстердам": 1, "netherlands": 1,
        "швейцария": 1, "цюрих": 1, "switzerland": 1,
        "турция": 3, "стамбул": 3, "turkey": 3,
        "португалия": 0, "лиссабон": 0, "portugal": 0,
        # Ближний Восток
        "оаэ": 4, "дубай": 4, "uae": 4, "dubai": 4,
        "израиль": 2, "тель-авив": 2, "israel": 2,
        "саудовская аравия": 3, "saudi": 3,
        # Азия
        "индия": 5, "дели": 5, "мумбаи": 5, "india": 5,
        "китай": 8, "пекин": 8, "шанхай": 8, "china": 8,
        "япония": 9, "токио": 9, "japan": 9,
        "корея": 9, "сеул": 9, "korea": 9,
        "таиланд": 7, "бангкок": 7, "thailand": 7,
        "индонезия": 7, "джакарта": 7, "indonesia": 7,
        "сингапур": 8, "singapore": 8,
        # Америка
        "сша": -5, "нью-йорк": -5, "usa": -5, "us": -5, "new york": -5,
        "лос-анджелес": -8, "los angeles": -8, "california": -8,
        "канада": -5, "торонто": -5, "canada": -5,
        "бразилия": -3, "сан-паулу": -3, "brazil": -3,
        "мексика": -6, "mexico": -6,
        "аргентина": -3, "буэнос-айрес": -3, "argentina": -3,
        # Океания
        "австралия": 10, "сидней": 10, "australia": 10,
    }
    for key, offset in tz_map.items():
        if key in country_lower:
            return offset
    return None


# ============ AUTO-BACKUP ============
async def auto_backup_loop():
    """Фоновая задача: автоматический бэкап JSON файлов"""
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL)
            create_backup()
        except Exception as e:
            logger.error(f"Ошибка auto-backup: {e}")

def create_backup():
    """Создаёт бэкап всех JSON файлов"""
    try:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_subdir = os.path.join(BACKUP_DIR, timestamp)
        os.makedirs(backup_subdir, exist_ok=True)

        json_files = glob.glob(os.path.join(DATA_DIR, "*.json"))
        for src in json_files:
            dst = os.path.join(backup_subdir, os.path.basename(src))
            shutil.copy2(src, dst)

        logger.info(f"Бэкап создан: {backup_subdir} ({len(json_files)} файлов)")

        # Удаляем старые бэкапы
        cleanup_old_backups()
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")

def cleanup_old_backups():
    """Удаляет старые бэкапы, оставляя BACKUP_MAX_FILES"""
    try:
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "*")))
        if len(backups) > BACKUP_MAX_FILES:
            for old in backups[:len(backups) - BACKUP_MAX_FILES]:
                shutil.rmtree(old, ignore_errors=True)
                logger.info(f"Удалён старый бэкап: {old}")
    except Exception as e:
        logger.error(f"Ошибка очистки бэкапов: {e}")


# ============ HEALTHCHECK ============
_healthcheck_data = {
    "status": "starting",
    "version": BOT_VERSION,
    "started_at": None,
    "last_check": None,
    "uptime_seconds": 0,
    "errors_last_hour": 0,
    "messages_processed": 0,
}

def update_healthcheck(status: str = "running", messages_processed: int = 0, error: bool = False):
    """Обновляет данные healthcheck"""
    now = datetime.utcnow().isoformat()
    _healthcheck_data["status"] = status
    _healthcheck_data["last_check"] = now
    if _healthcheck_data["started_at"] is None:
        _healthcheck_data["started_at"] = now
    if messages_processed:
        _healthcheck_data["messages_processed"] = messages_processed
    if error:
        _healthcheck_data["errors_last_hour"] += 1

    # Сохраняем в файл
    try:
        with open(HEALTHCHECK_FILE, "w", encoding="utf-8") as f:
            json.dump(_healthcheck_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_healthcheck_data() -> dict:
    """Возвращает данные healthcheck"""
    if _healthcheck_data["started_at"]:
        started = datetime.fromisoformat(_healthcheck_data["started_at"])
        _healthcheck_data["uptime_seconds"] = int((datetime.utcnow() - started).total_seconds())
    return _healthcheck_data

async def healthcheck_loop():
    """Фоновая задача: периодическое обновление healthcheck"""
    while True:
        try:
            await asyncio.sleep(HEALTHCHECK_INTERVAL)
            update_healthcheck(status="running")
            # Сбрасываем счётчик ошибок каждый час
            _healthcheck_data["errors_last_hour"] = 0
        except Exception as e:
            logger.error(f"Ошибка healthcheck loop: {e}")

async def start_healthcheck_server():
    """Запускает HTTP сервер для healthcheck на HEALTHCHECK_PORT"""
    try:
        from aiohttp import web

        async def health_handler(request):
            data = get_healthcheck_data()
            return web.json_response(data)

        app = web.Application()
        app.router.add_get("/health", health_handler)
        app.router.add_get("/", health_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", HEALTHCHECK_PORT)
        await site.start()
        logger.info(f"Healthcheck сервер запущен на порту {HEALTHCHECK_PORT}")
    except ImportError:
        logger.warning("aiohttp не установлен, healthcheck HTTP сервер отключён")
    except Exception as e:
        logger.error(f"Ошибка запуска healthcheck сервера: {e}")


def get_healthcheck_report() -> str:
    """Формирует текстовый отчёт для !здоровье команды"""
    data = get_healthcheck_data()
    uptime = data.get("uptime_seconds", 0)
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60

    return (
        f"HEALTHCHECK v{BOT_VERSION}\n"
        f"{'=' * 25}\n"
        f"Статус: {data['status']}\n"
        f"Аптайм: {hours}ч {minutes}м\n"
        f"Запущен: {data.get('started_at', 'N/A')}\n"
        f"Последняя проверка: {data.get('last_check', 'N/A')}\n"
        f"Ошибок за час: {data.get('errors_last_hour', 0)}\n"
        f"Сообщений обработано: {data.get('messages_processed', 0)}"
    )
