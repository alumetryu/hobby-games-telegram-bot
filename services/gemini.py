"""
Gemini API — обёртка с retry-логикой и rate limiting.
"""

import asyncio
import logging
import time

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

_client: genai.Client | None = None

# Rate limiting: минимальный интервал между запросами (секунды)
_MIN_REQUEST_INTERVAL = 2.0
_last_request_time: float = 0.0


def setup_gemini() -> None:
    global _client
    _client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info(f"Gemini инициализирован. Модель: {GEMINI_MODEL}")


async def _rate_limit() -> None:
    """Ограничивает частоту запросов к API."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        wait_time = _MIN_REQUEST_INTERVAL - elapsed
        logger.debug(f"Rate limit: жду {wait_time:.1f}с")
        await asyncio.sleep(wait_time)
    _last_request_time = time.monotonic()


@retry(
    retry=retry_if_exception_type((ClientError, ServerError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=5, min=10, max=60),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def ask_gemini(prompt: str, max_tokens: int = 2000) -> str:
    """
    Отправляет запрос к Gemini и возвращает текст ответа.
    При 429/5xx делает до 4 попыток с экспоненциальной задержкой (10-60 сек).
    """
    if _client is None:
        raise RuntimeError("Gemini не инициализирован. Вызовите setup_gemini().")

    await _rate_limit()

    try:
        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.4,
            ),
        )
        return response.text
    except (ClientError, ServerError) as exc:
        status = getattr(exc, "status_code", None)
        if status == 429:
            logger.warning(
                "Gemini API quota exceeded (429). "
                "Проверьте лимиты или перейдите на платный тариф."
            )
            raise
        if status and status >= 500:
            logger.warning(f"Gemini server error ({status}), retrying...")
            raise
        raise
