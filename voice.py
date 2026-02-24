"""
Голосовой модуль — ElevenLabs TTS/STT, ambient noise mixing, конвертация.
"""
import os
import random
import asyncio
import subprocess
import logging
from io import BytesIO
from elevenlabs.client import ElevenLabs
from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, AMBIENT_DIR, DEFAULT_VOICE_RATIO
from storage import user_preferences

logger = logging.getLogger("tg_agent")

# Инициализация ElevenLabs
elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)


# ============ VOICE RATIO ============
def should_send_voice(user_id: int = None) -> bool:
    """Определяет, отправлять ли голосовое (с учётом предпочтений)"""
    if user_id and user_id in user_preferences:
        pref = user_preferences[user_id]
        if pref.get("mode") == "text_only":
            return False
        if pref.get("mode") == "more_voice":
            return random.random() < pref.get("ratio", 0.7)
    return random.random() < DEFAULT_VOICE_RATIO


# ============ TYPING DELAY ============
def calc_typing_delay(text: str) -> float:
    """Рассчитывает задержку набора текста пропорционально длине"""
    length = len(text)
    if length < 50:
        return random.uniform(1.5, 3.0)
    elif length < 150:
        return random.uniform(3.0, 5.0)
    elif length < 300:
        return random.uniform(5.0, 8.0)
    else:
        return random.uniform(7.0, 12.0)


# ============ EMOTION TAGS ============
def add_emotion_tags(text: str) -> str:
    """Подготавливает текст для TTS.
    
    ElevenLabs v3 НЕ поддерживает кастомные теги типа [friendly], [calm] и т.д.
    Они озвучиваются буквально. Поэтому просто возвращаем чистый текст.
    Можно добавить паузы через '...' для более естественного звучания.
    """
    # Добавляем естественные паузы (многоточие) для длинных текстов
    if random.random() < 0.3 and ". " in text:
        text = text.replace(". ", "... ", 1)
    return text


# ============ КОНВЕРТАЦИЯ MP3 -> OGG OPUS ============
def convert_mp3_to_ogg_opus(mp3_path: str) -> str | None:
    """Конвертирует MP3 в OGG Opus для Telegram"""
    ogg_path = mp3_path.replace(".mp3", ".ogg")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "64k",
             "-vbr", "on", "-compression_level", "10", "-application", "voip", ogg_path],
            capture_output=True, timeout=15
        )
        if result.returncode == 0 and os.path.exists(ogg_path):
            logger.info(f"Конвертация MP3->OGG Opus: OK ({os.path.getsize(ogg_path)} bytes)")
            return ogg_path
        else:
            logger.error(f"ffmpeg ошибка: {result.stderr.decode()[:200]}")
            return None
    except Exception as e:
        logger.error(f"Ошибка конвертации: {e}")
        return None


# ============ AMBIENT NOISE MIXING ============
def mix_ambient_noise(voice_mp3_path: str) -> str | None:
    """Добавляет фоновый шум к голосовому"""
    ambient_files = [f for f in os.listdir(AMBIENT_DIR) if f.endswith('.mp3')] if os.path.exists(AMBIENT_DIR) else []
    if not ambient_files:
        return None
    ambient_file = os.path.join(AMBIENT_DIR, random.choice(ambient_files))
    output_path = voice_mp3_path.replace(".mp3", "_mixed.mp3")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", voice_mp3_path, "-i", ambient_file,
             "-filter_complex", "[1:a]volume=0.04,atrim=duration=120[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[out]",
             "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", output_path],
            capture_output=True, timeout=15
        )
        if result.returncode == 0 and os.path.exists(output_path):
            logger.info(f"Ambient mixed: {os.path.getsize(output_path)} bytes")
            return output_path
        else:
            logger.error(f"Ambient mix error: {result.stderr.decode()[:200]}")
            return None
    except Exception as e:
        logger.error(f"Ambient mix exception: {e}")
        return None


# ============ TEXT-TO-SPEECH ============
def text_to_voice_sync(text: str) -> bytes | None:
    """Синхронная генерация голосового через ElevenLabs"""
    clean_text = text.replace("[ЗАЯВКА_ПОЛУЧЕНА]", "").strip()
    if not clean_text:
        return None
    tagged_text = add_emotion_tags(clean_text)
    try:
        audio_generator = elevenlabs_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID, text=tagged_text,
            model_id="eleven_v3", output_format="mp3_44100_128",
        )
        audio_bytes = b"".join(audio_generator)
        logger.info(f"ElevenLabs v3: голосовое создано ({len(audio_bytes)} bytes)")
        return audio_bytes
    except Exception as e:
        logger.error(f"ElevenLabs ошибка: {e}")
        return None

async def text_to_voice(text: str) -> bytes | None:
    """Асинхронная обёртка для TTS"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, text_to_voice_sync, text)


# ============ SPEECH-TO-TEXT ============
def speech_to_text_sync(audio_bytes: bytes, file_name: str = "voice.ogg") -> str | None:
    """Синхронное распознавание речи через ElevenLabs Scribe"""
    try:
        audio_data = BytesIO(audio_bytes)
        audio_data.name = file_name
        transcription = elevenlabs_client.speech_to_text.convert(
            file=audio_data, model_id="scribe_v2", tag_audio_events=False,
        )
        text = transcription.text if hasattr(transcription, 'text') else str(transcription)
        logger.info(f"STT распознано: {text[:100]}...")
        return text.strip() if text else None
    except Exception as e:
        logger.error(f"ElevenLabs STT ошибка: {e}")
        return None

async def speech_to_text(audio_bytes: bytes, file_name: str = "voice.ogg") -> str | None:
    """Асинхронная обёртка для STT"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, speech_to_text_sync, audio_bytes, file_name)
