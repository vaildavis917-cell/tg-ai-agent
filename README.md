# Telegram AI Sales Agent v5.0

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![Claude](https://img.shields.io/badge/Claude-Sonnet_4-orange)](https://anthropic.com)
[![ElevenLabs](https://img.shields.io/badge/ElevenLabs-TTS%2FSTT-green)](https://elevenlabs.io)
[![Telethon](https://img.shields.io/badge/Telethon-userbot-9cf)](https://github.com/LonamiWebs/Telethon)

Модульный Telegram userbot для автоматизации продаж.
Claude AI генерирует ответы, ElevenLabs — голосовые, рыночные данные — в реальном времени.

> **Userbot** — работает от имени аккаунта, не бота. Использовать на свой риск, соблюдать ToS Telegram.

---

## Архитектура

```
tg-ai-agent/
├── main.py          # Точка входа
├── config.py        # Все настройки и константы
├── prompts.py       # Промпты Claude + A/B тесты
├── storage.py       # JSON-хранилище лидов/диалогов
├── ai.py            # Claude API + retry + Vision
├── voice.py         # ElevenLabs TTS/STT + ambient
├── market.py        # Рыночные данные (Yahoo/CoinGecko/ExchangeRate)
├── analytics.py     # Метрики + CSV экспорт
├── utils.py         # Время, backup, healthcheck
├── handlers.py      # Telegram events + команды менеджеров
└── requirements.txt
```

| Модуль | Строк | Отвечает за |
|--------|-------|-------------|
| `config.py` | ~110 | Настройки, пути, лимиты, валидация |
| `prompts.py` | ~337 | Промпты, A/B тесты, шаблоны |
| `storage.py` | ~320 | Хранилище, лимиты, парсинг заявок, atomic write |
| `market.py` | ~200 | Yahoo Finance -> CoinGecko -> ExchangeRate (async aiohttp) |
| `ai.py` | ~290 | Claude + retry backoff + мультиязычность + Vision retry |
| `voice.py` | ~160 | TTS/STT + ambient noise + конвертация |
| `analytics.py` | ~255 | Аналитика, трекинг, CSV |
| `utils.py` | ~253 | Время, часовые пояса, backup, healthcheck |
| `handlers.py` | ~886 | Events, FloodWait, команды менеджеров |
| `main.py` | ~100 | Инициализация, dotenv, валидация, запуск |

---

## Быстрый старт

### 1. Клонировать и установить зависимости

```bash
git clone https://github.com/vaildavis917-cell/tg-ai-agent
cd tg-ai-agent
pip install -r requirements.txt
```

### 2. Настроить переменные окружения

```bash
cp .env.example .env
nano .env  # заполнить все поля
```

**Обязательные переменные:**

| Переменная | Где взять |
|-----------|-----------|
| `TELEGRAM_API_ID` | [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_PHONE` | Номер аккаунта с `+` |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `ELEVENLABS_API_KEY` | [elevenlabs.io](https://elevenlabs.io) |
| `ELEVENLABS_VOICE_ID` | ID голоса в ElevenLabs |
| `FORWARD_GROUP_ID` | ID группы менеджеров (с `-100`) |

**Опциональные:**

| Переменная | По умолчанию | Описание |
|-----------|-------------|---------|
| `TELEGRAM_2FA_PASSWORD` | — | Пароль двухфакторки |
| `DEBUG_USERNAMES` | — | Username'ы для debug-команд (через запятую) |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Модель Claude |

### 3. Настроить промпты

Открыть `prompts.py` и заменить плейсхолдеры в `SYSTEM_PROMPT` на реальный промпт агента.

### 4. Запустить

```bash
python main.py
```

При первом запуске Telegram попросит код подтверждения.

---

## Команды

### В личных сообщениях (только DEBUG_USERNAMES)

| Команда | Действие |
|---------|---------|
| `!помощь` | Список всех команд |
| `!инфо` | Версия, модели, параметры |
| `!аналитика` | Статистика бота |
| `!экспорт` | CSV с лидами |
| `!здоровье` | Healthcheck + аптайм |
| `!абтест` | Статистика A/B теста |
| `!гс` | Тест голосового (TTS) |
| `!заявка` | Тест пересылки заявки в группу |
| `!первое` | Сброс + тест первого сообщения |
| `!статус` | Температура и статус лида |
| `!сброс` | Сброс диалога |
| `!фоллоуп` | Тест follow-up сообщения |
| `!блок` | Тест уведомления о блокировке |
| `!созвон` | Тест уведомления о согласии на звонок |

### В группе менеджеров (reply на заявку)

| Команда | Действие |
|---------|---------|
| `!` (пустой reply) | Статус лида |
| `!заблокировать` | Заблокировать лида |
| `!разблокировать` | Разблокировать лида |
| `!любой текст` | Push-сообщение клиенту от имени бота |

### В группе менеджеров (без reply)

| Команда | Действие |
|---------|---------|
| `!аналитика` | Статистика |
| `!экспорт` | CSV с лидами |
| `!здоровье` | Healthcheck |
| `!@username текст` | Команда конкретному лиду по username |

---

## Особенности

- **FloodWait** — авто-retry при Telegram rate limits
- **Claude retry + exponential backoff** — 3 попытки при RateLimit/Overloaded/5xx
- **A/B тестирование** — два варианта промптов, автоматическая статистика конверсий
- **Мультиязычность** — авто-определение языка, ответ на языке клиента
- **Голосовые** — 25% сообщений голосом (настраивается), клиент может попросить отключить
- **Ambient noise** — добавляет фоновый шум к голосовым (папка `ambient/`)
- **Рыночные данные** — Yahoo Finance с fallback на CoinGecko и ExchangeRate (полностью async)
- **Follow-up** — через 3ч, максимум 2 попытки, ночью не отправляет
- **Auto-backup** — каждый час, хранит 48 архивов
- **Atomic write** — запись JSON через tmp + rename, защита от corrupted файлов
- **Config validation** — проверка обязательных переменных при старте
- **Healthcheck** — HTTP :8080/health + команда `!здоровье`

---

## Troubleshooting

**Бот не стартует, ошибка переменных окружения**
-> Проверь `.env` файл, убедись что все обязательные переменные заполнены

**`FloodWaitError` в логах постоянно**
-> Снизь `MESSAGE_BATCH_DELAY` или уменьши `MAX_MESSAGES_PER_DAY` в `config.py`

**Голосовые не отправляются**
-> Проверь `ELEVENLABS_API_KEY` и `ELEVENLABS_VOICE_ID`, убедись что `ffmpeg` установлен

**Рыночные данные не обновляются**
-> Yahoo Finance периодически блокирует запросы. CoinGecko и ExchangeRate подхватят автоматически

**Healthcheck не отвечает**
-> `pip install aiohttp` и убедись порт 8080 открыт

---

## Требования

- Python 3.11+
- ffmpeg (для голосовых)
- Telegram аккаунт (не бот-токен)
