"""
Аналитика и статистика бота.
Команда !аналитика, экспорт в CSV/Excel, трекинг метрик.
"""
import os
import re
import csv
import json
import logging
from datetime import datetime, timedelta
from config import ANALYTICS_FILE, DATA_DIR, TIMEZONE_OFFSET
from storage import (
    conversations, lead_statuses, blocked_users,
    followup_tracker, username_to_userid,
    daily_message_count, daily_user_counts,
)
from prompts import get_ab_stats

logger = logging.getLogger("tg_agent")

# ============ АНАЛИТИЧЕСКИЕ ДАННЫЕ ============
analytics_data = {
    "total_messages_sent": 0,
    "total_messages_received": 0,
    "total_voice_sent": 0,
    "total_applications": 0,
    "total_call_agreements": 0,
    "total_blocks": 0,
    "total_followups_sent": 0,
    "daily_stats": {},  # {"2025-02-23": {"sent": 10, "received": 5, "applications": 1}}
    "hourly_activity": {},  # {"14": 25, "15": 30}
    "response_times": [],  # последние 100 времён ответа (секунды)
}


def load_analytics():
    global analytics_data
    try:
        if os.path.exists(ANALYTICS_FILE):
            with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            analytics_data.update(loaded)
            logger.info("Аналитика загружена")
    except Exception as e:
        logger.error(f"Ошибка загрузки аналитики: {e}")

def save_analytics():
    try:
        with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
            json.dump(analytics_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения аналитики: {e}")


# ============ ТРЕКИНГ СОБЫТИЙ ============
def track_message_sent(is_voice: bool = False):
    analytics_data["total_messages_sent"] += 1
    if is_voice:
        analytics_data["total_voice_sent"] += 1
    _track_daily("sent")
    _track_hourly()
    save_analytics()

def track_message_received():
    analytics_data["total_messages_received"] += 1
    _track_daily("received")
    save_analytics()

def track_application():
    analytics_data["total_applications"] += 1
    _track_daily("applications")
    save_analytics()

def track_call_agreement():
    analytics_data["total_call_agreements"] += 1
    _track_daily("call_agreements")
    save_analytics()

def track_block():
    analytics_data["total_blocks"] += 1
    _track_daily("blocks")
    save_analytics()

def track_followup():
    analytics_data["total_followups_sent"] += 1
    _track_daily("followups")
    save_analytics()

def track_response_time(seconds: float):
    analytics_data["response_times"].append(round(seconds, 2))
    # Храним только последние 100
    if len(analytics_data["response_times"]) > 100:
        analytics_data["response_times"] = analytics_data["response_times"][-100:]
    save_analytics()


def _track_daily(metric: str):
    today = _get_today_str()
    if today not in analytics_data["daily_stats"]:
        analytics_data["daily_stats"][today] = {}
    day = analytics_data["daily_stats"][today]
    day[metric] = day.get(metric, 0) + 1

def _track_hourly():
    hour = str(_get_moscow_hour())
    analytics_data["hourly_activity"][hour] = analytics_data["hourly_activity"].get(hour, 0) + 1

def _get_today_str() -> str:
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    return now.strftime("%Y-%m-%d")

def _get_moscow_hour() -> int:
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    return now.hour


# ============ ОТЧЁТ !аналитика ============
def get_analytics_report() -> str:
    """Формирует текстовый отчёт аналитики для группы менеджеров"""
    today = _get_today_str()
    today_stats = analytics_data["daily_stats"].get(today, {})

    # Подсчёт лидов по статусам
    status_counts = {}
    for uid, status in lead_statuses.items():
        status_counts[status] = status_counts.get(status, 0) + 1

    # Среднее время ответа
    resp_times = analytics_data.get("response_times", [])
    avg_response = sum(resp_times) / len(resp_times) if resp_times else 0

    # Конверсия
    total_leads = len(conversations)
    total_apps = analytics_data["total_applications"]
    conversion = (total_apps / total_leads * 100) if total_leads > 0 else 0

    # Пиковые часы
    hourly = analytics_data.get("hourly_activity", {})
    peak_hours = sorted(hourly.items(), key=lambda x: x[1], reverse=True)[:3]
    peak_str = ", ".join(f"{h}:00 ({c})" for h, c in peak_hours) if peak_hours else "нет данных"

    # Вчера
    yesterday = (datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET) - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_stats = analytics_data["daily_stats"].get(yesterday, {})

    report = (
        f"АНАЛИТИКА БОТА v5.0\n"
        f"{'=' * 30}\n"
        f"\n"
        f"СЕГОДНЯ ({today}):\n"
        f"  Отправлено: {today_stats.get('sent', 0)}\n"
        f"  Получено: {today_stats.get('received', 0)}\n"
        f"  Заявок: {today_stats.get('applications', 0)}\n"
        f"  Согласий на звонок: {today_stats.get('call_agreements', 0)}\n"
        f"  Follow-up: {today_stats.get('followups', 0)}\n"
        f"  Блокировок: {today_stats.get('blocks', 0)}\n"
        f"\n"
        f"ВЧЕРА ({yesterday}):\n"
        f"  Отправлено: {yesterday_stats.get('sent', 0)}\n"
        f"  Получено: {yesterday_stats.get('received', 0)}\n"
        f"  Заявок: {yesterday_stats.get('applications', 0)}\n"
        f"\n"
        f"ВСЕГО:\n"
        f"  Лидов в базе: {total_leads}\n"
        f"  Отправлено: {analytics_data['total_messages_sent']}\n"
        f"  Получено: {analytics_data['total_messages_received']}\n"
        f"  Голосовых: {analytics_data['total_voice_sent']}\n"
        f"  Заявок: {total_apps}\n"
        f"  Согласий на звонок: {analytics_data['total_call_agreements']}\n"
        f"  Блокировок: {analytics_data['total_blocks']}\n"
        f"  Follow-up: {analytics_data['total_followups_sent']}\n"
        f"\n"
        f"КОНВЕРСИЯ: {conversion:.1f}%\n"
        f"Ср. время ответа: {avg_response:.1f}с\n"
        f"Пиковые часы: {peak_str}\n"
        f"\n"
        f"СТАТУСЫ ЛИДОВ:\n"
    )

    for status, count in sorted(status_counts.items()):
        report += f"  {status}: {count}\n"

    # A/B тест
    ab_stats = get_ab_stats()
    report += f"\n{ab_stats}\n"

    report += f"\nЗаблокировано менеджером: {len(blocked_users)}"

    return report


# ============ ЭКСПОРТ В CSV ============
def export_leads_csv() -> str:
    """Экспортирует все лиды в CSV файл. Возвращает путь к файлу."""
    csv_path = os.path.join(DATA_DIR, f"leads_export_{_get_today_str()}.csv")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "User ID", "Username", "Статус", "Сообщений от клиента",
                "Сообщений от бота", "Первое сообщение", "Последнее сообщение",
                "Есть телефон", "Есть email",
            ])

            # Обратный маппинг username -> user_id
            uid_to_username = {v: k for k, v in username_to_userid.items()}

            for user_id, history in conversations.items():
                username = uid_to_username.get(user_id, "N/A")
                status = lead_statuses.get(user_id, "active")
                user_msgs = [m for m in history if m["role"] == "user"]
                bot_msgs = [m for m in history if m["role"] == "assistant"]

                # Определяем наличие контактов
                has_phone = any(re.search(r'\+?\d[\d\s\-]{7,}', m['content']) for m in user_msgs)
                has_email = any('@' in m['content'] and '.' in m['content'] for m in user_msgs)

                first_msg = user_msgs[0]["content"][:100] if user_msgs else ""
                last_msg = history[-1]["content"][:100] if history else ""

                writer.writerow([
                    user_id, f"@{username}", status,
                    len(user_msgs), len(bot_msgs),
                    first_msg, last_msg,
                    "Да" if has_phone else "Нет",
                    "Да" if has_email else "Нет",
                ])

        logger.info(f"CSV экспорт: {csv_path}")
        return csv_path
    except Exception as e:
        logger.error(f"Ошибка экспорта CSV: {e}")
        return ""


def export_conversations_csv(user_id: int = None) -> str:
    """Экспортирует переписки в CSV. Если user_id указан — только этого пользователя."""
    csv_path = os.path.join(DATA_DIR, f"conversations_export_{_get_today_str()}.csv")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["User ID", "Роль", "Сообщение"])

            target = {user_id: conversations[user_id]} if user_id and user_id in conversations else conversations
            for uid, history in target.items():
                for msg in history:
                    writer.writerow([uid, msg["role"], msg["content"][:500]])

        logger.info(f"Переписки экспортированы: {csv_path}")
        return csv_path
    except Exception as e:
        logger.error(f"Ошибка экспорта переписок: {e}")
        return ""
