"""
AI модуль — Claude API с retry logic и exponential backoff.
Включает мультиязычную поддержку и определение температуры лидов.
"""
import re
import asyncio
import base64
import logging
import random
from anthropic import AsyncAnthropic
import anthropic
from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_RETRIES, CLAUDE_BASE_DELAY,
)
from prompts import (
    get_system_prompt, get_first_message_template,
    MANAGER_PUSH_PROMPT, FOLLOWUP_PROMPT, LEAD_TEMP_PROMPT,
    PHOTO_ANALYSIS_PROMPT, STICKER_PROMPT,
    LANGUAGE_DETECT_PROMPT, MULTILANG_INSTRUCTION,
)
from market import get_market_context
from storage import (
    conversations, lead_statuses, save_conversations,
    add_conversation_message,
)

logger = logging.getLogger("tg_agent")

# Инициализация Claude (AsyncAnthropic — нативно асинхронный)
claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ============ RETRY LOGIC С EXPONENTIAL BACKOFF ============
async def claude_request_with_retry(model: str, max_tokens: int, system: str,
                                     messages: list, temperature: float = 1.0) -> str | None:
    """
    Отправляет запрос к Claude API с retry и exponential backoff.
    Обрабатывает: RateLimitError, APIError, OverloadedError.
    """
    for attempt in range(CLAUDE_MAX_RETRIES):
        try:
            response = await claude.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                temperature=temperature,
            )
            return response.content[0].text
        except anthropic.RateLimitError as e:
            delay = CLAUDE_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"Claude RateLimit (попытка {attempt + 1}/{CLAUDE_MAX_RETRIES}), ждём {delay:.1f}с: {e}")
            await asyncio.sleep(delay)
        except anthropic.APIStatusError as e:
            if e.status_code == 529:  # Overloaded
                delay = CLAUDE_BASE_DELAY * (2 ** attempt) + random.uniform(0, 2)
                logger.warning(f"Claude Overloaded (попытка {attempt + 1}/{CLAUDE_MAX_RETRIES}), ждём {delay:.1f}с")
                await asyncio.sleep(delay)
            elif e.status_code >= 500:
                delay = CLAUDE_BASE_DELAY * (2 ** attempt)
                logger.warning(f"Claude Server Error {e.status_code} (попытка {attempt + 1}/{CLAUDE_MAX_RETRIES}), ждём {delay:.1f}с")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Claude API ошибка (неретриабельная): {e}")
                return None
        except anthropic.APIConnectionError as e:
            delay = CLAUDE_BASE_DELAY * (2 ** attempt)
            logger.warning(f"Claude Connection Error (попытка {attempt + 1}/{CLAUDE_MAX_RETRIES}), ждём {delay:.1f}с: {e}")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"Claude неожиданная ошибка: {e}")
            return None

    logger.error(f"Claude API: все {CLAUDE_MAX_RETRIES} попыток исчерпаны")
    return None


# ============ ОПРЕДЕЛЕНИЕ ЯЗЫКА ============
async def detect_language(text: str) -> str:
    """Определяет язык текста через Claude"""
    # Быстрая эвристика для частых случаев
    if re.search(r'[а-яА-ЯёЁ]', text):
        # Проверяем украинский по характерным буквам
        if re.search(r'[іїєґІЇЄҐ]', text):
            return "ukrainian"
        return "russian"
    if re.search(r'[a-zA-Z]', text) and not re.search(r'[^\x00-\x7F]', text[:100]):
        return "english"
    # Для остальных языков — через Claude
    try:
        result = await claude_request_with_retry(
            model=CLAUDE_MODEL, max_tokens=10,
            system="Ты определяешь язык текста. Ответь ОДНИМ словом.",
            messages=[{"role": "user", "content": LANGUAGE_DETECT_PROMPT.format(text=text[:200])}],
        )
        if result:
            lang = result.strip().lower()
            known = ["russian", "english", "ukrainian", "spanish", "german",
                     "french", "arabic", "turkish", "portuguese", "chinese"]
            if lang in known:
                return lang
    except Exception as e:
        logger.error(f"Ошибка определения языка: {e}")
    return "russian"  # default


# ============ ОСНОВНОЙ AI ОТВЕТ ============
async def get_ai_response(user_id: int, user_message: str) -> tuple[str, bool]:
    """
    Генерирует ответ Claude для пользователя.
    Возвращает (текст_ответа, is_first_message).
    """
    is_first_message = user_id not in conversations or len(conversations.get(user_id, [])) == 0

    add_conversation_message(user_id, "user", user_message)

    if is_first_message:
        template = get_first_message_template(user_id)
        add_conversation_message(user_id, "assistant", template)
        logger.info(f"Первое сообщение для {user_id}: {template}")
        return (template, True)

    # Формируем системный промпт
    system = get_system_prompt(user_id)

    # Мультиязычность: определяем язык
    lang = await detect_language(user_message)
    if lang != "russian":
        lang_names = {
            "english": "английском", "ukrainian": "украинском",
            "spanish": "испанском", "german": "немецком",
            "french": "французском", "arabic": "арабском",
            "turkish": "турецком", "portuguese": "португальском",
            "chinese": "китайском",
        }
        lang_name = lang_names.get(lang, lang)
        system += MULTILANG_INSTRUCTION.format(language=lang_name)
        logger.info(f"Мультиязычность: {user_id} пишет на {lang}")

    # Добавляем рыночные данные
    market_ctx = await get_market_context()
    if market_ctx:
        system += market_ctx

    # Напоминаем Claude что это продолжение диалога (не приветствовать заново)
    msg_count = len(conversations.get(user_id, []))
    if msg_count > 2:
        system += f"\n\n[КОНТЕКСТ] Это ПРОДОЛЖЕНИЕ диалога (уже {msg_count} сообщений в истории). НЕ приветствуй заново. Продолжай разговор по контексту."

    # Добавляем контекст собранных данных
    status = lead_statuses.get(user_id)
    if status == "data_collected":
        system += """\n\n[ВАЖНО] Данные этого клиента УЖЕ СОБРАНЫ (имя, телефон, время). Заявка отправлена.
НЕ спрашивай имя/телефон/время заново. Просто общайся естественно.
Если клиент спрашивает про звонок -- подтверди что менеджер свяжется в указанное время.
Если клиент хочет изменить время -- прими новое время и скажи что передашь команде."""

    # Запрос к Claude с retry
    ai_text = await claude_request_with_retry(
        model=CLAUDE_MODEL, max_tokens=300,
        system=system, messages=conversations[user_id],
    )

    if ai_text:
        add_conversation_message(user_id, "assistant", ai_text)
        logger.info(f"Claude ответ для {user_id}: {ai_text[:100]}...")
        return (ai_text, False)
    else:
        return ("Секунду, сейчас отвечу)", False)


# ============ PUSH ОТ МЕНЕДЖЕРА ============
async def get_manager_push_response(user_id: int, manager_request: str) -> str | None:
    history = conversations.get(user_id, [])
    push_system = MANAGER_PUSH_PROMPT.format(manager_request=manager_request)
    messages = []
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": f"[ВНУТРЕННЯЯ КОМАНДА] Менеджер просит написать клиенту: {manager_request}"})

    ai_text = await claude_request_with_retry(
        model=CLAUDE_MODEL, max_tokens=300,
        system=push_system, messages=messages,
    )

    if ai_text:
        add_conversation_message(user_id, "assistant", ai_text)
        logger.info(f"Push-сообщение для {user_id}: {ai_text[:100]}...")
        return ai_text
    return None


# ============ FOLLOW-UP ============
async def get_followup_response(user_id: int, attempt: int) -> str | None:
    history = conversations.get(user_id, [])
    followup_system = FOLLOWUP_PROMPT.format(attempt=attempt)
    messages = []
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": f"[ВНУТРЕННЯЯ КОМАНДА] Клиент не отвечает. Напиши follow-up (попытка {attempt})"})

    ai_text = await claude_request_with_retry(
        model=CLAUDE_MODEL, max_tokens=200,
        system=followup_system, messages=messages,
    )

    if ai_text:
        add_conversation_message(user_id, "assistant", ai_text)
        return ai_text
    return None


# ============ ТЕМПЕРАТУРА ЛИДА ============
async def analyze_lead_temperature(user_id: int) -> str:
    history = conversations.get(user_id, [])
    if len(history) < 2:
        return "НОВЫЙ"
    context_msgs = history[-10:]
    dialog_text = "\n".join([f"{'Клиент' if m['role']=='user' else 'Томас'}: {m['content']}" for m in context_msgs])

    result = await claude_request_with_retry(
        model=CLAUDE_MODEL, max_tokens=10,
        system=LEAD_TEMP_PROMPT,
        messages=[{"role": "user", "content": dialog_text}],
    )

    if result:
        temp = result.strip().upper()
        if temp in ["ГОРЯЧИЙ", "ТЕПЛЫЙ", "ХОЛОДНЫЙ"]:
            return temp
    return "ТЕПЛЫЙ"


# ============ АНАЛИЗ ФОТО (Claude Vision) ============
async def analyze_photo(user_id: int, photo_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    if user_id not in conversations:
        conversations[user_id] = []
    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")
    add_conversation_message(user_id, "user", "[Клиент отправил фото]")

    try:
        messages_with_photo = list(conversations[user_id][:-1])
        messages_with_photo.append({
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": photo_b64}},
                {"type": "text", "text": "Клиент прислал это фото. Ответь естественно."}
            ]
        })
        # Vision запрос с retry
        for attempt in range(2):
            try:
                response = await claude.messages.create(
                    model=CLAUDE_MODEL, max_tokens=300,
                    system=PHOTO_ANALYSIS_PROMPT, messages=messages_with_photo,
                )
                ai_text = response.content[0].text
                add_conversation_message(user_id, "assistant", ai_text)
                logger.info(f"Vision ответ для {user_id}: {ai_text[:100]}...")
                return ai_text
            except anthropic.RateLimitError:
                logger.warning(f"Vision RateLimit (попытка {attempt + 1}/2), ждём 5с")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Ошибка Claude Vision: {e}")
                break
        return "Интересное фото) Расскажи подробнее что имеешь в виду?"
    except Exception as e:
        logger.error(f"Ошибка подготовки Vision: {e}")
        return "Интересное фото) Расскажи подробнее что имеешь в виду?"


# ============ ОБРАБОТКА СТИКЕРОВ/GIF (фикс дублирования #3) ============
async def handle_sticker_gif(user_id: int) -> str:
    """Генерирует ответ на стикер или GIF"""
    add_conversation_message(user_id, "user", "[Клиент отправил стикер/GIF]")

    # Берём историю ПОСЛЕ добавления — она уже содержит новое сообщение
    history = conversations.get(user_id, [])
    messages = history[-6:]

    ai_text = await claude_request_with_retry(
        model=CLAUDE_MODEL, max_tokens=100,
        system=STICKER_PROMPT, messages=messages,
    )

    if ai_text:
        add_conversation_message(user_id, "assistant", ai_text)
        return ai_text
    return "Ну что, к делу?)"
