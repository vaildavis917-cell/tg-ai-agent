"""
Конфигурация бота Farallon Capital Agent v5.0
Все настройки, пути, лимиты и константы.
"""
import os

# ============ TELEGRAM ============
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
TWO_FA_PASSWORD = os.getenv("TELEGRAM_2FA_PASSWORD", None)

# ============ API КЛЮЧИ ============
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# ============ ГРУППА МЕНЕДЖЕРОВ ============
FORWARD_GROUP_ID = int(os.getenv("FORWARD_GROUP_ID", "0"))

# ============ ГОЛОС ============
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

# ============ МОДЕЛЬ CLAUDE ============
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# ============ ЧЕРНЫЙ СПИСОК ============
BLACKLIST_IDS = set()

# ============ ДЕБАГ ============
DEBUG_USERNAMES = set()  # add your debug usernames here

# ============ ЛИМИТЫ ============
MAX_MESSAGES_PER_DAY = 200
MAX_MESSAGES_PER_USER_DAY = 50
MAX_HISTORY = 20

# ============ FOLLOW-UP ============
FOLLOWUP_DELAY_HOURS = 3
FOLLOWUP_MAX_ATTEMPTS = 2
FOLLOWUP_CHECK_INTERVAL = 300  # 5 минут

# ============ ЧАСОВОЙ ПОЯС (UTC+3 Москва) ============
TIMEZONE_OFFSET = 3

# ============ ГОЛОСОВЫЕ НАСТРОЙКИ ============
DEFAULT_VOICE_RATIO = 0.25  # 25% голос / 75% текст

# ============ БАТЧИНГ ============
MESSAGE_BATCH_DELAY = 3  # секунды

# ============ РЫНОК ============
MARKET_UPDATE_INTERVAL = 1800  # 30 минут

# ============ ПУТИ ============
BASE_DIR = os.getenv("BOT_BASE_DIR", os.path.join(os.path.expanduser("~"), "tg_agent"))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
AMBIENT_DIR = os.path.join(BASE_DIR, "ambient")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

CONVERSATIONS_FILE = os.path.join(DATA_DIR, "conversations.json")
LEADS_MAP_FILE = os.path.join(DATA_DIR, "leads_map.json")
LEAD_STATUS_FILE = os.path.join(DATA_DIR, "lead_status.json")
FOLLOWUP_FILE = os.path.join(DATA_DIR, "followup.json")
PREFERENCES_FILE = os.path.join(DATA_DIR, "preferences.json")
BLOCKED_FILE = os.path.join(DATA_DIR, "blocked_users.json")
MARKET_CACHE_FILE = os.path.join(DATA_DIR, "market_cache.json")
ANALYTICS_FILE = os.path.join(DATA_DIR, "analytics.json")
AB_TEST_FILE = os.path.join(DATA_DIR, "ab_tests.json")
HEALTHCHECK_FILE = os.path.join(DATA_DIR, "healthcheck.json")

# Создаём директории
for d in [LOGS_DIR, DATA_DIR, AMBIENT_DIR, BACKUP_DIR]:
    os.makedirs(d, exist_ok=True)

# ============ РАСПИСАНИЕ АКТИВНОСТИ (по часовым поясам клиентов) ============
# Ночное время (MSK) — не отправляем сообщения
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7

# ============ RETRY / BACKOFF ============
CLAUDE_MAX_RETRIES = 3
CLAUDE_BASE_DELAY = 2  # секунды
FLOODWAIT_MAX_RETRIES = 5

# ============ BACKUP ============
BACKUP_INTERVAL = 3600  # каждый час
BACKUP_MAX_FILES = 48  # хранить 48 бэкапов (2 дня)

# ============ HEALTHCHECK ============
HEALTHCHECK_INTERVAL = 300  # 5 минут
HEALTHCHECK_PORT = 8080

# ============ A/B ТЕСТИРОВАНИЕ ============
AB_TEST_ENABLED = True

# ============ ВЕРСИЯ ============
BOT_VERSION = "5.0"


# ============ ВАЛИДАЦИЯ ============
_REQUIRED = {
    "TELEGRAM_API_ID": API_ID,
    "TELEGRAM_API_HASH": API_HASH,
    "TELEGRAM_PHONE": PHONE,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
}

def validate_config():
    """Проверяет что все обязательные переменные заполнены"""
    missing = [k for k, v in _REQUIRED.items() if not v or v == 0]
    if missing:
        raise EnvironmentError(
            f"\n\u274c Отсутствуют обязательные переменные: {', '.join(missing)}\n"
            f"Проверь .env файл или export переменные.\n"
            f"См. .env.example для списка всех переменных."
        )
