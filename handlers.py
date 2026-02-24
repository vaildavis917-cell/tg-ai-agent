"""
Обработчики Telegram событий.
FloodWait handling, отправка сообщений, обработка команд.
"""
import os
import re
import random
import asyncio
import logging
import time as _time
from datetime import datetime, timezone
from telethon import TelegramClient, events
from telethon.tl.types import PeerUser
from telethon.errors import (
    UserIsBlockedError,
    PeerIdInvalidError,
    InputUserDeactivatedError,
    UserDeactivatedBanError,
    ChatWriteForbiddenError,
    FloodWaitError,
)
from config import (
    API_ID, API_HASH, PHONE, TWO_FA_PASSWORD,
    FORWARD_GROUP_ID, BLACKLIST_IDS, DEBUG_USERNAMES,
    BASE_DIR, CLAUDE_MODEL, ELEVENLABS_VOICE_ID,
    DEFAULT_VOICE_RATIO, MESSAGE_BATCH_DELAY,
    FOLLOWUP_DELAY_HOURS, FOLLOWUP_MAX_ATTEMPTS, FOLLOWUP_CHECK_INTERVAL,
    FLOODWAIT_MAX_RETRIES, MAX_MESSAGES_PER_DAY, MAX_MESSAGES_PER_USER_DAY,
    BOT_VERSION,
)
from storage import (
    conversations, leads_map, user_to_group, username_to_userid,
    followup_tracker, lead_statuses, blocked_users,
    message_buffer, pending_tasks,
    save_conversations, save_leads_map, save_followup, save_lead_statuses,
    set_lead_status, block_user, unblock_user,
    check_limits, increment_counts, detect_preference,
    parse_application_data, validate_application,
    extract_username_from_text, detect_call_agreement,
    add_conversation_message,
)
from ai import (
    get_ai_response, get_manager_push_response, get_followup_response,
    analyze_lead_temperature, analyze_photo, handle_sticker_gif,
)
from voice import (
    text_to_voice, speech_to_text,
    convert_mp3_to_ogg_opus, mix_ambient_noise,
    should_send_voice, calc_typing_delay,
)
from utils import (
    get_moscow_now, is_night_time, get_healthcheck_report,
    update_healthcheck, is_appropriate_time_for_user,
)
from analytics import (
    track_message_sent, track_message_received, track_application,
    track_call_agreement, track_block, track_followup, track_response_time,
    get_analytics_report, export_leads_csv, export_conversations_csv,
)
from prompts import get_ab_stats, record_ab_conversion

logger = logging.getLogger("tg_agent")

# ============ TELEGRAM CLIENT ============
client = TelegramClient(os.path.join(BASE_DIR, "session"), API_ID, API_HASH)


# ============ CLEANUP HELPER ============
def _cleanup_files(*paths):
    """Безопасно удаляет временные файлы."""
    for p in paths:
        if p:
            try:
                os.remove(p)
            except OSError:
                pass


# ============ FLOODWAIT HANDLING ============
async def safe_send_message(user_id, text=None, file=None, voice_note=False, reply_to=None):
    """Отправляет сообщение с обработкой FloodWait"""
    for attempt in range(FLOODWAIT_MAX_RETRIES):
        try:
            if file:
                return await client.send_file(user_id, file, voice_note=voice_note)
            else:
                return await client.send_message(user_id, text, reply_to=reply_to)
        except FloodWaitError as e:
            wait_time = e.seconds + 1
            logger.warning(f"FloodWait: ждём {wait_time}с (попытка {attempt + 1}/{FLOODWAIT_MAX_RETRIES})")
            await asyncio.sleep(wait_time)
        except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
                UserDeactivatedBanError, ChatWriteForbiddenError):
            raise  # Эти ошибки не ретриабельны
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            if attempt < FLOODWAIT_MAX_RETRIES - 1:
                await asyncio.sleep(2)
            else:
                raise
    return None


async def safe_send_to_group(text, reply_to=None):
    """Отправляет сообщение в группу с обработкой FloodWait"""
    if not FORWARD_GROUP_ID:
        return None
    for attempt in range(FLOODWAIT_MAX_RETRIES):
        try:
            return await client.send_message(FORWARD_GROUP_ID, text, reply_to=reply_to)
        except FloodWaitError as e:
            wait_time = e.seconds + 1
            logger.warning(f"FloodWait (группа): ждём {wait_time}с")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Ошибка отправки в группу: {e}")
            if attempt < FLOODWAIT_MAX_RETRIES - 1:
                await asyncio.sleep(2)
            else:
                raise
    return None


# ============ ОТПРАВКА ОДНОГО СООБЩЕНИЯ ============
async def send_single_message(user_id: int, text: str, allow_voice: bool = True,
                               username: str = None, force_voice: bool = False):
    """Отправляет одно сообщение (текст или голос) с детектом блокировки"""
    typing_delay = calc_typing_delay(text)

    try:
        if (force_voice or (allow_voice and should_send_voice(user_id))) and len(text) < 500:
            async with client.action(user_id, 'record-audio'):
                voice_data = await text_to_voice(text)
                await asyncio.sleep(random.uniform(1.0, 2.5))
            if voice_data:
                ts = int(datetime.now(timezone.utc).timestamp())
                mp3_path = f"/tmp/voice_{user_id}_{ts}.mp3"
                mixed_path = None
                ogg_path = None
                try:
                    with open(mp3_path, "wb") as f:
                        f.write(voice_data)
                    mixed_path = mix_ambient_noise(mp3_path)
                    final_mp3 = mixed_path if mixed_path else mp3_path
                    ogg_path = convert_mp3_to_ogg_opus(final_mp3)
                    if ogg_path:
                        await safe_send_message(user_id, file=ogg_path, voice_note=True)
                    else:
                        await safe_send_message(user_id, file=final_mp3, voice_note=True)
                finally:
                    _cleanup_files(mp3_path, mixed_path, ogg_path)
                increment_counts(user_id)
                track_message_sent(is_voice=True)
                logger.info(f"Отправлено голосовое для {user_id}")
                return

        # Текстовое сообщение
        async with client.action(user_id, 'typing'):
            await asyncio.sleep(typing_delay)
        await safe_send_message(user_id, text=text)
        increment_counts(user_id)
        track_message_sent(is_voice=False)
        logger.info(f"Отправлен текст для {user_id}")

    except UserIsBlockedError:
        logger.warning(f"Клиент {user_id} ЗАБЛОКИРОВАЛ бота!")
        set_lead_status(user_id, "client_blocked")
        track_block()
        await notify_group_blocked(user_id, username, "blocked_by_client")
        raise
    except (PeerIdInvalidError, InputUserDeactivatedError, UserDeactivatedBanError):
        logger.warning(f"Клиент {user_id} удалил чат или аккаунт деактивирован!")
        set_lead_status(user_id, "chat_deleted")
        track_block()
        await notify_group_blocked(user_id, username, "chat_deleted")
        raise
    except ChatWriteForbiddenError:
        logger.warning(f"Нет доступа к чату с {user_id}")
        set_lead_status(user_id, "chat_deleted")
        track_block()
        await notify_group_blocked(user_id, username, "chat_deleted")
        raise


# ============ ОТПРАВКА СООБЩЕНИЯ (с разбивкой по ||) ============
async def send_message_to_user(user_id: int, text: str, username: str = None, force_voice: bool = False):
    """Разбивает ответ по || и отправляет как отдельные сообщения"""
    # Детект метки [ГОЛОС]
    if "[ГОЛОС]" in text:
        text = text.replace("[ГОЛОС]", "").strip()
        force_voice = True
        logger.info(f"[ГОЛОС] метка обнаружена для {user_id}")

    parts = [p.strip() for p in text.split('||') if p.strip()]
    if len(parts) > 2:
        parts = parts[:2]

    if len(parts) <= 1:
        await send_single_message(user_id, text.strip(), username=username, force_voice=force_voice)
        return

    voice_idx = random.randint(0, len(parts) - 1) if should_send_voice(user_id) else -1
    for i, part in enumerate(parts):
        if force_voice:
            allow_voice = True
            fv = True
        else:
            allow_voice = (i == voice_idx) and len(part) < 500
            fv = False
        await send_single_message(user_id, part, allow_voice=allow_voice, username=username, force_voice=fv)
        if i < len(parts) - 1:
            delay = calc_typing_delay(parts[i + 1]) + random.uniform(2.0, 4.0)
            await asyncio.sleep(delay)


# ============ УВЕДОМЛЕНИЯ В ГРУППУ ============
async def notify_group_blocked(user_id: int, username: str, reason: str):
    """Сообщает в группу менеджеров о блокировке/удалении чата"""
    if not FORWARD_GROUP_ID:
        return
    group_msg_id = user_to_group.get(user_id)
    try:
        reason_text = {
            "blocked_by_client": "!! Клиент ЗАБЛОКИРОВАЛ бота",
            "chat_deleted": "!! Клиент УДАЛИЛ чат",
            "user_deactivated": "!! Аккаунт клиента УДАЛЕН/забанен",
        }.get(reason, reason)
        msg = f"{reason_text}\n@{username or 'N/A'} (ID: {user_id})"
        await safe_send_to_group(msg, reply_to=group_msg_id)
        logger.info(f"Уведомление в группу: {reason} для {user_id}")
    except Exception as e:
        logger.error(f"Ошибка уведомления группы о блокировке: {e}")

async def notify_group_event(user_id: int, username: str, event_type: str, details: str = ""):
    """Уведомляет группу о важных событиях"""
    if not FORWARD_GROUP_ID:
        return
    group_msg_id = user_to_group.get(user_id)
    try:
        event_labels = {
            "call_agreed": "\U0001f4de Клиент СОГЛАСИЛСЯ на звонок",
            "push_result": "\U0001f4e8 Итог пуша",
        }
        label = event_labels.get(event_type, event_type)
        msg = f"{label}\n@{username or 'N/A'} (ID: {user_id})"
        if details:
            msg += f"\n{details[:300]}"
        await safe_send_to_group(msg, reply_to=group_msg_id)
        logger.info(f"Событие '{event_type}' для {user_id} отправлено в группу")
    except Exception as e:
        logger.error(f"Ошибка уведомления группы о событии: {e}")


# ============ ПЕРЕСЫЛКА ЗАЯВКИ В ГРУППУ ============
async def forward_to_group(user_id: int, username: str, ai_response: str):
    """Парсит данные заявки и отправляет в группу менеджеров"""
    if not FORWARD_GROUP_ID:
        logger.warning("FORWARD_GROUP_ID не задан")
        return
    try:
        parsed = parse_application_data(ai_response)
        if not parsed:
            logger.warning(f"Не удалось распарсить заявку для {user_id}")
            temperature = await analyze_lead_temperature(user_id)
            now = get_moscow_now().strftime("%d.%m.%Y %H:%M")
            msg = f"[?] НОВАЯ ЗАЯВКА [{temperature}]\nTelegram: @{username or 'N/A'} (ID: {user_id})\nДата: {now}\n---\n{ai_response[:500]}"
            sent = await safe_send_to_group(msg)
            if sent:
                leads_map[sent.id] = user_id
                user_to_group[user_id] = sent.id
                save_leads_map()
            return

        is_valid, error_msg = validate_application(parsed)
        if not is_valid:
            logger.warning(f"Невалидная заявка от {user_id}: {error_msg}")
            return

        temperature = await analyze_lead_temperature(user_id)
        temp_icon = {"ГОРЯЧИЙ": "[!!!]", "ТЕПЛЫЙ": "[!!]", "ХОЛОДНЫЙ": "[!]", "НОВЫЙ": "[?]", "НЕИЗВЕСТНО": "[?]"}.get(temperature, "[?]")
        now = get_moscow_now().strftime("%d.%m.%Y %H:%M")
        msg = (
            f"{temp_icon} НОВАЯ ЗАЯВКА [{temperature}]\n"
            f"---\n"
            f"Telegram: @{username or 'нет username'} (ID: {user_id})\n"
            f"Дата: {now}\n"
            f"---\n"
            f"Имя: {parsed.get('name', '-')}\n"
            f"Телефон: {parsed.get('phone', '-')}\n"
            f"Email: {parsed.get('email', '-')}\n"
            f"Страна: {parsed.get('country', '-')}\n"
            f"Время звонка: {parsed.get('call_time', '-')}"
        )
        sent = await safe_send_to_group(msg)
        if sent:
            leads_map[sent.id] = user_id
            user_to_group[user_id] = sent.id
            save_leads_map()
        track_application()
        record_ab_conversion(user_id)
        logger.info(f"Заявка от {user_id} [{temperature}] переслана в группу")
    except Exception as e:
        logger.error(f"Ошибка пересылки в группу: {e}")


# ============ FOLLOW-UP TRACKER ============
def update_followup_tracker(user_id: int, is_user_message: bool):
    now_iso = get_moscow_now().isoformat()
    if is_user_message:
        followup_tracker[user_id] = {"last_msg_time": now_iso, "attempts": 0, "completed": False}
    else:
        if user_id in followup_tracker:
            followup_tracker[user_id]["last_msg_time"] = now_iso
    save_followup()

async def check_followups():
    """Фоновая задача: проверка и отправка follow-up сообщений"""
    while True:
        try:
            await asyncio.sleep(FOLLOWUP_CHECK_INTERVAL)
            if is_night_time():
                continue
            now = get_moscow_now()
            for user_id, data in list(followup_tracker.items()):
                if data.get("completed", False):
                    continue
                if user_id in blocked_users:
                    continue
                status = lead_statuses.get(user_id, "active")
                if status in ("blocked", "client_blocked", "chat_deleted"):
                    continue
                attempts = data.get("attempts", 0)
                if attempts >= FOLLOWUP_MAX_ATTEMPTS:
                    followup_tracker[user_id]["completed"] = True
                    save_followup()
                    continue
                # Безопасный парсинг datetime (#10 fix)
                try:
                    last_time = datetime.fromisoformat(data["last_msg_time"])
                    # Если naive — считаем что это Moscow time, приводим к тому же формату
                    if last_time.tzinfo is not None and now.tzinfo is None:
                        last_time = last_time.replace(tzinfo=None)
                    elif last_time.tzinfo is None and now.tzinfo is not None:
                        last_time = last_time.replace(tzinfo=now.tzinfo)
                except (ValueError, KeyError):
                    logger.warning(f"Невалидный last_msg_time для {user_id}, пропускаем")
                    continue
                hours_passed = (now - last_time).total_seconds() / 3600
                if hours_passed >= FOLLOWUP_DELAY_HOURS:
                    history = conversations.get(user_id, [])
                    if history and history[-1]["role"] == "assistant":
                        limit_reason = check_limits(user_id)
                        if limit_reason:
                            continue
                        new_attempt = attempts + 1
                        logger.info(f"Follow-up #{new_attempt} для {user_id} (прошло {hours_passed:.1f}ч)")
                        followup_text = await get_followup_response(user_id, new_attempt)
                        if followup_text:
                            try:
                                await send_message_to_user(user_id, followup_text)
                            except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
                                    UserDeactivatedBanError, ChatWriteForbiddenError):
                                followup_tracker[user_id]["completed"] = True
                                save_followup()
                                continue
                            followup_tracker[user_id]["attempts"] = new_attempt
                            followup_tracker[user_id]["last_msg_time"] = now.isoformat()
                            save_followup()
                            track_followup()
                            logger.info(f"Follow-up #{new_attempt} отправлен {user_id}")
                        await asyncio.sleep(random.uniform(30, 120))
        except Exception as e:
            logger.error(f"Ошибка в check_followups: {e}")
            update_healthcheck(error=True)


# ============ СТАТУС ЛИДА ============
async def get_lead_status_report(user_id: int) -> str:
    try:
        status = lead_statuses.get(user_id, "active")
        history = conversations.get(user_id, [])
        user_msgs = [m for m in history if m['role'] == 'user']
        bot_msgs = [m for m in history if m['role'] == 'assistant']
        has_phone = any(re.search(r'\+?\d[\d\s\-]{7,}', m['content']) for m in user_msgs)
        has_email = any('@' in m['content'] and '.' in m['content'] for m in user_msgs)

        if status == "blocked":
            stage = "[X] ЗАБЛОКИРОВАН менеджером"
        elif status == "client_blocked":
            stage = "[X] Клиент заблокировал бота"
        elif status == "chat_deleted":
            stage = "[X] Клиент удалил чат"
        elif status == "data_collected":
            stage = "[OK] Данные собраны, заявка отправлена"
        elif len(history) == 0:
            stage = "[--] Новый лид, нет переписки"
        elif len(history) <= 4:
            stage = "[..] Начало разговора"
        elif has_phone or has_email:
            stage = "[+] Сбор данных (есть контакты)"
        else:
            stage = "[~] В процессе обработки"

        last_msgs = ""
        if history:
            for m in history[-6:]:
                role = "Клиент" if m['role'] == 'user' else "Бот"
                text = m['content'][:80] + ('...' if len(m['content']) > 80 else '')
                last_msgs += f"\n  {role}: {text}"

        fu = followup_tracker.get(user_id, followup_tracker.get(str(user_id), {}))
        fu_info = ""
        if fu:
            fu_info = f"\nFollow-up: {fu.get('attempts', 0)} попыток"

        temperature = await analyze_lead_temperature(user_id)

        report = (
            f"Статус лида (ID: {user_id})\n"
            f"---\n"
            f"Этап: {stage}\n"
            f"Температура: {temperature}\n"
            f"Сообщений: {len(user_msgs)} от клиента, {len(bot_msgs)} от бота"
            f"{fu_info}\n"
            f"---\n"
            f"Последние сообщения:{last_msgs}"
        )
        return report
    except Exception as e:
        logger.error(f"Ошибка формирования отчёта: {e}")
        return f"Ошибка получения статуса: {e}"


# ============ ПОИСК USER_ID ============
async def find_user_id_by_username(username: str):
    uid = username_to_userid.get(username.lower())
    if uid:
        return uid
    try:
        entity = await client.get_entity(username)
        if entity:
            username_to_userid[username.lower()] = entity.id
            return entity.id
    except Exception as e:
        logger.warning(f"Не удалось найти пользователя @{username}: {e}")
    return None


# ============ ОБРАБОТКА КОМАНД МЕНЕДЖЕРА ============
async def handle_group_command(event, user_id: int, manager_request: str):
    req_lower = manager_request.lower().strip()

    # Блокировка
    if req_lower in ("заблокировать", "блок", "block", "бан", "ban"):
        block_user(user_id)
        await event.reply(f"Лид {user_id} ЗАБЛОКИРОВАН. Бот больше не будет ему отвечать.")
        return

    # Разблокировка
    if req_lower in ("разблокировать", "разблок", "unblock", "анблок", "unban"):
        unblock_user(user_id)
        await event.reply(f"Лид {user_id} РАЗБЛОКИРОВАН.")
        return

    # Статус
    status_keywords = ["что с этим", "что с ним", "статус", "на каком этапе", "что там", "как дела", "инфо", "что с клиентом", "?"]
    is_status_request = any(kw in req_lower for kw in status_keywords) or req_lower == ""

    if is_status_request:
        report = await get_lead_status_report(user_id)
        await event.reply(report)
        return

    # Push сообщение
    logger.info(f"Команда менеджера для {user_id}: {manager_request}")
    push_text = await get_manager_push_response(user_id, manager_request)
    if not push_text:
        await event.reply("Ошибка генерации сообщения")
        return
    try:
        await send_message_to_user(user_id, push_text)
        await event.reply(f"Отправлено клиенту: {push_text[:100]}...")
    except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
            UserDeactivatedBanError, ChatWriteForbiddenError):
        await event.reply("Не удалось отправить -- клиент заблокировал бота или удалил чат")
    except Exception as e:
        logger.error(f"Ошибка отправки push: {e}")
        await event.reply(f"Ошибка отправки: {e}")


# ============ ОБРАБОТКА БАТЧА СООБЩЕНИЙ ============
async def process_batched_messages(user_id: int):
    """Ждёт MESSAGE_BATCH_DELAY секунд, собирает все сообщения и отвечает"""
    try:
        await asyncio.sleep(MESSAGE_BATCH_DELAY)

        buf = message_buffer.pop(user_id, None)
        pending_tasks.pop(user_id, None)
        if not buf or not buf["messages"]:
            return

        username = buf["username"]
        combined_message = " ".join(buf["messages"])

        logger.info(f"Батч от {user_id}: {len(buf['messages'])} сообщений -> '{combined_message[:100]}'")

        # Трекинг
        track_message_received()
        start_time = _time.time()

        # Детектим предпочтения
        detect_preference(user_id, combined_message)
        update_followup_tracker(user_id, is_user_message=True)

        # Детект запроса голосового от клиента
        voice_request_keywords = [
            "голосов", "войс", "voice", "запиши голос", "скажи голосом",
            "дай голос", "отправь голос", "аудио", "скажи вслух",
            "говори", "послушать тебя", "хочу услышать",
        ]
        force_voice_requested = any(kw in combined_message.lower() for kw in voice_request_keywords)
        if force_voice_requested:
            logger.info(f"Клиент {user_id} запросил голосовое")

        ai_response, is_first = await get_ai_response(user_id, combined_message)

        # Трекинг времени ответа
        response_time = _time.time() - start_time
        track_response_time(response_time)

        has_application = "[ЗАЯВКА_ПОЛУЧЕНА]" in ai_response
        clean_response = ai_response.replace("[ЗАЯВКА_ПОЛУЧЕНА]", "").strip()

        # Детект согласия на звонок
        if detect_call_agreement(ai_response, combined_message):
            track_call_agreement()
            await notify_group_event(user_id, username, "call_agreed", combined_message[:200])

        # force_voice если первое сообщение ИЛИ клиент явно попросил голосовое
        should_force_voice = is_first or force_voice_requested

        try:
            await send_message_to_user(user_id, clean_response, username=username, force_voice=should_force_voice)
        except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
                UserDeactivatedBanError, ChatWriteForbiddenError):
            return
        update_followup_tracker(user_id, is_user_message=False)
        update_healthcheck(messages_processed=1)

        if has_application:
            set_lead_status(user_id, "data_collected")
            await forward_to_group(user_id, username, ai_response)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Ошибка обработки батча от {user_id}: {e}")
        update_healthcheck(error=True)


# ============ РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ============
def register_handlers():
    """Регистрирует все обработчики событий Telegram"""

    # ---- ЛИЧНЫЕ СООБЩЕНИЯ ----
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def handler(event):
        user_id = event.sender_id

        if user_id in BLACKLIST_IDS:
            return
        if user_id in blocked_users:
            return

        limit_reason = check_limits(user_id)
        if limit_reason:
            logger.warning(f"Лимит для {user_id}: {limit_reason}")
            return

        # Read receipts
        try:
            await client.send_read_acknowledge(event.chat_id, event.message)
        except Exception:
            pass

        sender = await event.get_sender()
        username = getattr(sender, 'username', None)
        first_name = getattr(sender, 'first_name', '') or ''

        if username:
            username_to_userid[username.lower()] = user_id

        # ---- СТИКЕРЫ / GIF ----
        if event.sticker or (event.document and event.document.mime_type and
                             'gif' in event.document.mime_type.lower()):
            logger.info(f"Стикер/GIF от {first_name} (@{username}, ID:{user_id})")
            track_message_received()
            try:
                update_followup_tracker(user_id, is_user_message=True)
                ai_response = await handle_sticker_gif(user_id)
                clean_response = ai_response.replace("[ЗАЯВКА_ПОЛУЧЕНА]", "").strip()
                is_first = user_id not in conversations or len(conversations.get(user_id, [])) <= 2
                await send_message_to_user(user_id, clean_response, username=username, force_voice=is_first)
                update_followup_tracker(user_id, is_user_message=False)
            except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
                    UserDeactivatedBanError, ChatWriteForbiddenError):
                pass
            except Exception as e:
                logger.error(f"Ошибка обработки стикера/GIF от {user_id}: {e}")
            return

        # ---- ГОЛОСОВЫЕ СООБЩЕНИЯ (STT) ----
        if event.voice or event.audio:
            logger.info(f"Голосовое от {first_name} (@{username}, ID:{user_id})")
            track_message_received()
            try:
                voice_data = await event.download_media(bytes)
                if voice_data:
                    transcribed_text = await speech_to_text(voice_data)
                    if transcribed_text:
                        logger.info(f"Распознано от {user_id}: {transcribed_text[:100]}")
                        update_followup_tracker(user_id, is_user_message=True)
                        ai_response, is_first = await get_ai_response(user_id, transcribed_text)
                        has_application = "[ЗАЯВКА_ПОЛУЧЕНА]" in ai_response
                        clean_response = ai_response.replace("[ЗАЯВКА_ПОЛУЧЕНА]", "").strip()
                        try:
                            await send_message_to_user(user_id, clean_response, username=username, force_voice=is_first)
                        except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
                                UserDeactivatedBanError, ChatWriteForbiddenError):
                            return
                        update_followup_tracker(user_id, is_user_message=False)
                        if has_application:
                            set_lead_status(user_id, "data_collected")
                            await forward_to_group(user_id, username, ai_response)
                        return
            except Exception as e:
                logger.error(f"Ошибка обработки голосового от {user_id}: {e}")
            return

        # ---- ФОТО (Claude Vision) ----
        if event.photo:
            logger.info(f"Фото от {first_name} (@{username}, ID:{user_id})")
            track_message_received()
            try:
                photo_data = await event.download_media(bytes)
                if photo_data:
                    update_followup_tracker(user_id, is_user_message=True)
                    ai_response = await analyze_photo(user_id, photo_data, "image/jpeg")
                    clean_response = ai_response.replace("[ЗАЯВКА_ПОЛУЧЕНА]", "").strip()
                    try:
                        await send_message_to_user(user_id, clean_response, username=username)
                    except (UserIsBlockedError, PeerIdInvalidError, InputUserDeactivatedError,
                            UserDeactivatedBanError, ChatWriteForbiddenError):
                        return
                    update_followup_tracker(user_id, is_user_message=False)
                    return
            except Exception as e:
                logger.error(f"Ошибка обработки фото от {user_id}: {e}")
            return

        # ---- ДЕБАГ-КОМАНДЫ ----
        user_message = event.raw_text
        if not user_message:
            return

        if user_message.startswith("!") and username and username.lower() in DEBUG_USERNAMES:
            cmd = user_message[1:].strip().lower()
            logger.info(f"DEBUG команда от @{username}: {cmd}")

            if cmd == "помощь" or cmd == "help":
                help_text = (
                    "DEBUG команды:\n"
                    "---\n"
                    "!гс -- тест голосового\n"
                    "!заявка -- тест заявки\n"
                    "!первое -- тест первого сообщения\n"
                    "!статус -- статус лида\n"
                    "!сброс -- сброс диалога\n"
                    "!фоллоуп -- тест follow-up\n"
                    "!блок -- тест уведомления о блоке\n"
                    "!созвон -- тест согласия на звонок\n"
                    "!инфо -- инфо о боте\n"
                    "!аналитика -- аналитика бота\n"
                    "!экспорт -- экспорт лидов в CSV\n"
                    "!здоровье -- healthcheck\n"
                    "!абтест -- A/B тест статистика\n"
                    "!помощь -- это сообщение"
                )
                await event.reply(help_text)
                return

            if cmd == "гс" or cmd == "voice":
                test_text = "Это тестовое голосовое сообщение для проверки TTS."
                try:
                    await send_single_message(user_id, test_text, allow_voice=True, username=username, force_voice=True)
                    await event.reply("[DEBUG] ГС отправлено")
                except Exception as e:
                    await event.reply(f"[DEBUG] Ошибка ГС: {e}")
                return

            if cmd == "заявка" or cmd == "app":
                test_response = (
                    "[ЗАЯВКА_ПОЛУЧЕНА]\n"
                    "Имя: Тест Дебагов\n"
                    "Телефон: +380991234567\n"
                    "Email: test@gmail.com\n"
                    "Страна: Украина, Киев\n"
                    "Время: завтра в 14:00\n"
                    "---\n"
                    "Отлично, передам команде)"
                )
                await forward_to_group(user_id, username, test_response)
                await event.reply("[DEBUG] Тестовая заявка отправлена в группу")
                return

            if cmd == "первое" or cmd == "first":
                conversations.pop(user_id, None)
                save_conversations()
                ai_response, is_first = await get_ai_response(user_id, "Привет")
                clean_response = ai_response.replace("[ЗАЯВКА_ПОЛУЧЕНА]", "").strip()
                try:
                    await send_message_to_user(user_id, clean_response, username=username, force_voice=True)
                    await event.reply(f"[DEBUG] Первое сообщение отправлено (is_first={is_first})")
                except Exception as e:
                    await event.reply(f"[DEBUG] Ошибка: {e}")
                return

            if cmd == "статус" or cmd == "status":
                report = await get_lead_status_report(user_id)
                await event.reply(f"[DEBUG] {report}")
                return

            if cmd == "сброс" or cmd == "reset":
                conversations.pop(user_id, None)
                lead_statuses.pop(user_id, None)
                followup_tracker.pop(user_id, None)
                followup_tracker.pop(str(user_id), None)
                user_to_group.pop(user_id, None)
                save_conversations()
                save_lead_statuses()
                save_followup()
                await event.reply("[DEBUG] Диалог сброшен.")
                return

            if cmd == "фоллоуп" or cmd == "followup":
                followup_text = await get_followup_response(user_id, 1)
                if followup_text:
                    try:
                        await send_message_to_user(user_id, followup_text, username=username)
                        await event.reply(f"[DEBUG] Follow-up отправлен: {followup_text[:100]}")
                    except Exception as e:
                        await event.reply(f"[DEBUG] Ошибка follow-up: {e}")
                else:
                    await event.reply("[DEBUG] Не удалось сгенерировать follow-up")
                return

            if cmd == "блок" or cmd == "block":
                await notify_group_blocked(user_id, username, "blocked_by_client")
                await event.reply("[DEBUG] Тестовое уведомление 'блок' отправлено")
                return

            if cmd == "созвон" or cmd == "call":
                await notify_group_event(user_id, username, "call_agreed", "Тестовое согласие на звонок")
                await event.reply("[DEBUG] Тестовое уведомление 'созвон' отправлено")
                return

            if cmd == "инфо" or cmd == "info":
                moscow_now = get_moscow_now()
                info_text = (
                    f"Bot v{BOT_VERSION} DEBUG\n"
                    f"---\n"
                    f"Модель: {CLAUDE_MODEL}\n"
                    f"Голос: {ELEVENLABS_VOICE_ID}\n"
                    f"Voice ratio: {DEFAULT_VOICE_RATIO * 100:.0f}%\n"
                    f"Батчинг: {MESSAGE_BATCH_DELAY}с\n"
                    f"Follow-up: {FOLLOWUP_DELAY_HOURS}ч, макс {FOLLOWUP_MAX_ATTEMPTS}\n"
                    f"---\n"
                    f"Диалогов: {len(conversations)}\n"
                    f"Заблокировано: {len(blocked_users)}\n"
                    f"Статусов: {len(lead_statuses)}\n"
                    f"Группа: {FORWARD_GROUP_ID}\n"
                    f"Время (MSK): {moscow_now.strftime('%H:%M %d.%m.%Y')}"
                )
                await event.reply(info_text)
                return

            # НОВЫЕ КОМАНДЫ
            if cmd == "аналитика" or cmd == "analytics":
                report = get_analytics_report()
                await event.reply(report)
                return

            if cmd == "экспорт" or cmd == "export":
                csv_path = export_leads_csv()
                if csv_path and os.path.exists(csv_path):
                    await client.send_file(user_id, csv_path, caption="[DEBUG] Экспорт лидов")
                else:
                    await event.reply("[DEBUG] Ошибка экспорта")
                return

            if cmd == "здоровье" or cmd == "health":
                report = get_healthcheck_report()
                await event.reply(report)
                return

            if cmd == "абтест" or cmd == "abtest":
                stats = get_ab_stats()
                await event.reply(f"[DEBUG] {stats}")
                return

            await event.reply(f"[DEBUG] Неизвестная команда: !{cmd}\nНапиши !помощь для списка")
            return

        # ---- ТЕКСТОВЫЕ СООБЩЕНИЯ (с батчингом) ----
        logger.info(f"Входящее от {first_name} (@{username}, ID:{user_id}): {user_message[:100]}")

        if user_id not in message_buffer:
            message_buffer[user_id] = {"messages": [], "username": username, "first_name": first_name}
        message_buffer[user_id]["messages"].append(user_message)

        if user_id in pending_tasks:
            pending_tasks[user_id].cancel()

        pending_tasks[user_id] = asyncio.create_task(process_batched_messages(user_id))

    # ---- РЕПЛАИ В ГРУППЕ ----
    @client.on(events.NewMessage(func=lambda e: e.is_reply and not e.is_private))
    async def group_reply_handler(event):
        if not FORWARD_GROUP_ID:
            return
        chat_id = event.chat_id
        if chat_id != FORWARD_GROUP_ID:
            return
        message_text = event.raw_text
        if not message_text or not message_text.startswith("!"):
            return
        manager_request = message_text[1:].strip()

        reply_msg = await event.get_reply_message()
        if not reply_msg:
            return
        me = await client.get_me()
        if reply_msg.sender_id != me.id:
            return

        original_msg_id = reply_msg.id
        user_id = leads_map.get(original_msg_id)

        if not user_id:
            id_match = re.search(r'ID:\s*(\d+)', reply_msg.raw_text or '')
            if id_match:
                user_id = int(id_match.group(1))
            else:
                uname = extract_username_from_text(reply_msg.raw_text or '')
                if uname:
                    user_id = await find_user_id_by_username(uname)
                if not user_id:
                    await event.reply("Не удалось найти клиента для этой заявки")
                    return

        await handle_group_command(event, user_id, manager_request)

    # ---- КОМАНДЫ В ГРУППЕ БЕЗ REPLY ----
    @client.on(events.NewMessage(func=lambda e: not e.is_reply and not e.is_private))
    async def group_direct_command_handler(event):
        if not FORWARD_GROUP_ID:
            return
        chat_id = event.chat_id
        if chat_id != FORWARD_GROUP_ID:
            return
        message_text = event.raw_text
        if not message_text or not message_text.startswith("!"):
            return
        command_text = message_text[1:].strip()

        # Команда !аналитика в группе
        if command_text.lower() in ("аналитика", "analytics", "статистика", "stats"):
            report = get_analytics_report()
            await event.reply(report)
            return

        # Команда !экспорт в группе
        if command_text.lower() in ("экспорт", "export"):
            csv_path = export_leads_csv()
            if csv_path and os.path.exists(csv_path):
                await client.send_file(event.chat_id, csv_path, caption="Экспорт лидов")
            else:
                await event.reply("Ошибка экспорта")
            return

        # Команда !здоровье в группе
        if command_text.lower() in ("здоровье", "health", "healthcheck"):
            report = get_healthcheck_report()
            await event.reply(report)
            return

        if not command_text:
            return

        username = extract_username_from_text(command_text)
        if not username:
            return

        manager_request = re.sub(r'(?:https?://)?t\.me/[a-zA-Z0-9_]+', '', command_text)
        manager_request = re.sub(r'@[a-zA-Z0-9_]+', '', manager_request).strip()

        user_id = await find_user_id_by_username(username)
        if not user_id:
            await event.reply(f"Не удалось найти клиента @{username}")
            return

        await handle_group_command(event, user_id, manager_request)

    logger.info("Все обработчики зарегистрированы")
