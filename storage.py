"""
Хранилище данных — загрузка/сохранение JSON файлов.
Все данные хранятся в памяти и периодически сохраняются на диск.
"""
import os
import re
import json
import asyncio
import logging
from collections import defaultdict
from datetime import date
from config import (
    CONVERSATIONS_FILE, LEADS_MAP_FILE, LEAD_STATUS_FILE,
    FOLLOWUP_FILE, PREFERENCES_FILE, BLOCKED_FILE,
    MAX_HISTORY, MAX_MESSAGES_PER_DAY, MAX_MESSAGES_PER_USER_DAY,
)

logger = logging.getLogger("tg_agent")

# ============ ГЛОБАЛЬНЫЕ ДАННЫЕ ============
conversations = {}
leads_map = {}          # {group_msg_id: user_id}
user_to_group = {}      # {user_id: group_msg_id}
username_to_userid = {} # {username: user_id}
followup_tracker = {}
lead_statuses = {}      # {user_id: status_string}
blocked_users = set()
user_preferences = {}
message_buffer = {}     # {user_id: {"messages": [], "username": str, "first_name": str}}
pending_tasks = {}      # {user_id: asyncio.Task}

# ============ СЧЁТЧИКИ ============
daily_message_count = 0
daily_user_counts = defaultdict(int)
last_count_date = date.today()


# ============ ЗАГРУЗКА ============
def load_conversations():
    global conversations
    try:
        if os.path.exists(CONVERSATIONS_FILE):
            with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            conversations.clear()
            conversations.update({int(k): v for k, v in data.items()})
            logger.info(f"Загружено {len(conversations)} диалогов")
    except Exception as e:
        logger.error(f"Ошибка загрузки диалогов: {e}")
        conversations.clear()

def load_leads_map():
    global leads_map, user_to_group
    try:
        if os.path.exists(LEADS_MAP_FILE):
            with open(LEADS_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            leads_map.clear()
            leads_map.update({int(k): int(v) for k, v in data.items()})
            user_to_group.clear()
            user_to_group.update({v: k for k, v in leads_map.items()})
            logger.info(f"Загружено {len(leads_map)} связок лид-сообщение")
    except Exception as e:
        logger.error(f"Ошибка загрузки leads_map: {e}")
        leads_map.clear()
        user_to_group.clear()

def load_lead_statuses():
    global lead_statuses
    try:
        if os.path.exists(LEAD_STATUS_FILE):
            with open(LEAD_STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            lead_statuses.clear()
            lead_statuses.update({int(k): v for k, v in data.items()})
            logger.info(f"Загружено {len(lead_statuses)} статусов лидов")
    except Exception as e:
        logger.error(f"Ошибка загрузки статусов: {e}")

def load_followup():
    global followup_tracker
    try:
        if os.path.exists(FOLLOWUP_FILE):
            with open(FOLLOWUP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            followup_tracker.clear()
            followup_tracker.update({int(k): v for k, v in data.items()})
            logger.info(f"Загружено {len(followup_tracker)} записей follow-up")
    except Exception as e:
        logger.error(f"Ошибка загрузки followup: {e}")
        followup_tracker.clear()

def load_preferences():
    global user_preferences
    try:
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            user_preferences.clear()
            user_preferences.update({int(k): v for k, v in data.items()})
            logger.info(f"Загружено {len(user_preferences)} предпочтений")
    except Exception as e:
        logger.error(f"Ошибка загрузки предпочтений: {e}")
        user_preferences.clear()

def load_blocked_users():
    global blocked_users
    try:
        if os.path.exists(BLOCKED_FILE):
            with open(BLOCKED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            blocked_users = set(int(x) for x in data)
            logger.info(f"Загружено {len(blocked_users)} заблокированных лидов")
    except Exception as e:
        logger.error(f"Ошибка загрузки blocked_users: {e}")


# ============ ЗАЩИТА ОТ RACE CONDITION ============
_save_lock = asyncio.Lock()


# ============ СОХРАНЕНИЕ ============
def _write_json(path: str, data: dict | list):
    """Atomic-подобная запись: пишем во временный файл, потом rename"""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def save_conversations():
    try:
        data = {str(k): v for k, v in conversations.items()}
        _write_json(CONVERSATIONS_FILE, data)
    except Exception as e:
        logger.error(f"Ошибка сохранения диалогов: {e}")

def save_leads_map():
    try:
        data = {str(k): v for k, v in leads_map.items()}
        _write_json(LEADS_MAP_FILE, data)
    except Exception as e:
        logger.error(f"Ошибка сохранения leads_map: {e}")

def save_lead_statuses():
    try:
        _write_json(LEAD_STATUS_FILE, {str(k): v for k, v in lead_statuses.items()})
    except Exception as e:
        logger.error(f"Ошибка сохранения статусов: {e}")

def save_followup():
    try:
        _write_json(FOLLOWUP_FILE, {str(k): v for k, v in followup_tracker.items()})
    except Exception as e:
        logger.error(f"Ошибка сохранения followup: {e}")

def save_preferences():
    try:
        _write_json(PREFERENCES_FILE, {str(k): v for k, v in user_preferences.items()})
    except Exception as e:
        logger.error(f"Ошибка сохранения предпочтений: {e}")

def save_blocked_users():
    try:
        _write_json(BLOCKED_FILE, list(blocked_users))
    except Exception as e:
        logger.error(f"Ошибка сохранения blocked_users: {e}")


# ============ ОПЕРАЦИИ С ДАННЫМИ ============
def set_lead_status(user_id: int, status: str):
    lead_statuses[user_id] = status
    save_lead_statuses()
    logger.info(f"Статус лида {user_id}: {status}")

def block_user(user_id: int):
    blocked_users.add(user_id)
    save_blocked_users()
    set_lead_status(user_id, "blocked")
    logger.info(f"Лид {user_id} ЗАБЛОКИРОВАН менеджером")

def unblock_user(user_id: int):
    blocked_users.discard(user_id)
    save_blocked_users()
    set_lead_status(user_id, "active")
    logger.info(f"Лид {user_id} РАЗБЛОКИРОВАН менеджером")

def add_conversation_message(user_id: int, role: str, content: str):
    if user_id not in conversations:
        conversations[user_id] = []
    conversations[user_id].append({"role": role, "content": content})
    if len(conversations[user_id]) > MAX_HISTORY:
        conversations[user_id] = conversations[user_id][-MAX_HISTORY:]
    save_conversations()


# ============ ПРЕДПОЧТЕНИЯ ============
def detect_preference(user_id: int, message: str):
    msg_lower = message.lower()
    text_keywords = ["пиши текстом", "не надо голосовых", "текстом пожалуйста", "лучше текстом",
                     "не отправляй голосовые", "без голосовых", "только текст", "пиши пожалуйста",
                     "text only", "no voice", "text please"]
    voice_keywords = ["голосом", "отправляй голосовые", "лучше голосом", "голосовые лучше",
                      "говори голосом", "записывай голосовые", "больше голосовых",
                      "voice please", "send voice", "more voice"]
    for kw in text_keywords:
        if kw in msg_lower:
            user_preferences[user_id] = {"mode": "text_only"}
            save_preferences()
            logger.info(f"Предпочтение {user_id}: ТОЛЬКО ТЕКСТ")
            return
    for kw in voice_keywords:
        if kw in msg_lower:
            user_preferences[user_id] = {"mode": "more_voice", "ratio": 0.7}
            save_preferences()
            logger.info(f"Предпочтение {user_id}: БОЛЬШЕ ГОЛОСА (70%)")
            return


# ============ ЛИМИТЫ ============
def reset_daily_counts_if_needed():
    global daily_message_count, daily_user_counts, last_count_date
    today = date.today()
    if today != last_count_date:
        logger.info(f"Новый день ({today}). Сброс счётчиков. Вчера отправлено: {daily_message_count} сообщений")
        daily_message_count = 0
        daily_user_counts = defaultdict(int)
        last_count_date = today

def check_limits(user_id: int) -> str | None:
    reset_daily_counts_if_needed()
    if daily_message_count >= MAX_MESSAGES_PER_DAY:
        return f"Дневной лимит ({MAX_MESSAGES_PER_DAY}) исчерпан"
    if daily_user_counts[user_id] >= MAX_MESSAGES_PER_USER_DAY:
        return f"Лимит для пользователя ({MAX_MESSAGES_PER_USER_DAY}/день) исчерпан"
    return None

def increment_counts(user_id: int):
    global daily_message_count
    daily_message_count += 1
    daily_user_counts[user_id] += 1


# ============ ПАРСИНГ ЗАЯВОК ============
def parse_application_data(ai_response: str) -> dict | None:
    try:
        data = {}
        field_map = {
            "имя": "name", "телефон": "phone", "email": "email",
            "страна": "country", "время": "call_time"
        }
        for line in ai_response.split("\n"):
            line = line.strip()
            for ru_key, en_key in field_map.items():
                if line.lower().startswith(ru_key + ":"):
                    value = line.split(":", 1)[1].strip()
                    if value:
                        data[en_key] = value
                    break
        if "name" in data and "phone" in data:
            return data
        return None
    except Exception as e:
        logger.error(f"Ошибка парсинга заявки: {e}")
        return None

def validate_application(data: dict) -> tuple[bool, str]:
    errors = []
    name = data.get("name", "")
    if len(name) < 2 or not re.search(r'[a-zA-Zа-яА-Я]', name):
        errors.append("невалидное имя")
    phone = data.get("phone", "")
    digits = re.sub(r'[^\d]', '', phone)
    if len(digits) < 8:
        errors.append("невалидный телефон")
    email = data.get("email", "")
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        errors.append("невалидный email")
    if errors:
        return False, ", ".join(errors)
    return True, ""


# ============ УТИЛИТЫ ============
def extract_username_from_text(text: str):
    match = re.search(r'(?:https?://)?t\.me/([a-zA-Z0-9_]+)', text)
    if match:
        return match.group(1).lower()
    match = re.search(r'@([a-zA-Z0-9_]+)', text)
    if match:
        return match.group(1).lower()
    return None

def detect_call_agreement(ai_response: str, user_message: str) -> bool:
    user_lower = user_message.lower()
    
    # Исключаем ложные срабатывания (запрос голосового, не звонка)
    voice_keywords = ["голосов", "войс", "voice", "аудио", "голосом"]
    if any(vk in user_lower for vk in voice_keywords):
        return False
    
    call_signals = [
        "давай звонок", "давай созвон", "давай позвон",
        "запиши на звонок", "запиши на консультацию", "записаться на звонок",
        "готов поговорить", "готов к звонку",
        "можно звонок", "хочу звонок", "хочу созвон",
        "когда звонок", "когда созвон",
        "давай на звонке",
        "начать работу", "начнём работать", "начнем работать",
        "оставлю данные", "оставить данные",
        "let's call", "schedule a call", "book a call",
        "i'm ready", "sign me up",
    ]
    return any(signal in user_lower for signal in call_signals)


def load_all():
    """Загружает все данные из файлов"""
    load_conversations()
    load_leads_map()
    load_lead_statuses()
    load_followup()
    load_preferences()
    load_blocked_users()
