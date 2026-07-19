"""
BrowserManager — потокобезопасный синглтон для управления Playwright-браузером.

Ключевые решения:
  • asyncio.Lock() — только один поток меняет состояние браузера одновременно.
  • Параллельные поиски работают через отдельные страницы одного контекста.
  • Сессия проверяется по таймеру (не на каждый запрос), чтобы не тратить время.
  • page.close() всегда в блоке finally — нет утечек страниц.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Playwright,
)

from config import CS_URL, CS_EMAIL, CS_PASSWORD
from constants import SESSION_CHECK_INTERVAL_SEC

logger = logging.getLogger(__name__)


class BrowserManager:
    """Singleton-менеджер Playwright-браузера."""

    _instance: "BrowserManager | None" = None

    def __new__(cls) -> "BrowserManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._ready = False
        return cls._instance

    def __init__(self) -> None:
        if self._ready:
            return
        self._lock: asyncio.Lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._last_check: datetime = datetime.min
        self._ready = True

    # ── Публичный API ──────────────────────────────────────────────────────────

    async def ensure_session(self) -> None:
        """
        Гарантирует, что браузер запущен и сессия активна.
        Проверка выполняется не чаще SESSION_CHECK_INTERVAL_SEC.
        """
        interval = timedelta(seconds=SESSION_CHECK_INTERVAL_SEC)
        if datetime.now() - self._last_check < interval:
            return  # Сессия свежая, не трогаем

        async with self._lock:
            # Double-check после получения лока
            if datetime.now() - self._last_check < interval:
                return

            if not self._is_alive():
                await self._launch()
                await self._login()
                return

            await self._verify_and_relogin()

    async def get_context(self) -> BrowserContext:
        """
        Возвращает готовый BrowserContext.
        Запускает браузер если он упал.
        """
        if self._is_alive():
            return self._context  # type: ignore[return-value]

        async with self._lock:
            if not self._is_alive():
                await self._launch()
                await self._login()
            return self._context  # type: ignore[return-value]

    async def force_relogin(self) -> None:
        """Принудительный перезапуск сессии (по команде /relogin)."""
        async with self._lock:
            if not self._is_alive():
                await self._launch()
            try:
                await self._login()
            except Exception as exc:
                # Если логин упал из-за упавшего контекста — полный перезапуск
                if "Target page, context or browser has been closed" in str(exc):
                    logger.warning("Контекст упал при логине, полный перезапуск...")
                    await self._teardown()
                    await self._launch()
                    await self._login()
                else:
                    raise

    async def close(self) -> None:
        """Корректное завершение: закрывает браузер и Playwright."""
        async with self._lock:
            await self._teardown()

    # ── Внутренние методы ──────────────────────────────────────────────────────

    def _is_alive(self) -> bool:
        try:
            return self._browser is not None and self._browser.is_connected()
        except Exception:
            return False

    def is_alive(self) -> bool:
        """Публичный метод проверки состояния браузера."""
        return self._is_alive()

    async def _launch(self) -> None:
        """Запускает Playwright и Chromium."""
        await self._teardown()
        logger.info("Запуск браузера...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        logger.info("Браузер запущен.")

    async def _login(self) -> None:
        """Выполняет вход в CloudShop. Всегда закрывает страницу в finally."""
        logger.info("Вхожу в CloudShop...")
        page = await self._context.new_page()  # type: ignore[union-attr]
        try:
            await page.goto(
                f"{CS_URL}/login",
                wait_until="networkidle",
                timeout=60_000,
            )
            await page.wait_for_selector(
                'input[type="password"], input[placeholder*="ароль" i]',
                timeout=30_000,
            )
            # Логин
            await page.fill(
                'input[type="email"], input[name="email"],'
                'input[placeholder*="mail" i], input[placeholder*="Email" i],'
                'input[placeholder*="телефон" i], input[placeholder*="phone" i]',
                CS_EMAIL,
            )
            await page.wait_for_timeout(400)
            # Пароль
            await page.fill(
                'input[type="password"], input[name="password"],'
                'input[placeholder*="ароль" i]',
                CS_PASSWORD,
            )
            await page.wait_for_timeout(400)
            # Кнопка входа
            await page.click(
                'button[type="submit"], button:has-text("ВОЙТИ"),'
                'button:has-text("Войти"), input[type="submit"]'
            )
            await page.wait_for_url(
                lambda url: "/login" not in url,
                timeout=20_000,
            )
            self._last_check = datetime.now()
            logger.info(f"Вход выполнен. URL: {page.url}")
        except Exception as exc:
            logger.error(f"Ошибка входа: {exc}", exc_info=True)
            try:
                await page.screenshot(path="login_error.png")
            except Exception:
                pass
            raise
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _verify_and_relogin(self) -> None:
        """Проверяет, не истекла ли сессия, и перелогинивается при необходимости."""
        page = await self._context.new_page()  # type: ignore[union-attr]
        try:
            await page.goto(
                f"{CS_URL}/pos/home",
                wait_until="networkidle",
                timeout=30_000,
            )
            if "/login" in page.url:
                logger.warning("Сессия истекла — перелогиниваюсь...")
                await page.close()
                await self._login()
            else:
                self._last_check = datetime.now()
                logger.debug("Сессия активна.")
        except Exception as exc:
            logger.error(f"Ошибка проверки сессии: {exc}")
            try:
                await page.close()
            except Exception:
                pass
            # Если ошибка связана с контекстом — полный перезапуск
            if "Target page, context or browser has been closed" in str(exc):
                logger.warning("Контекст браузера упал, полный перезапуск...")
                await self._teardown()
            await self._login()
        else:
            try:
                await page.close()
            except Exception:
                pass

    async def _teardown(self) -> None:
        """Закрывает браузер и Playwright без исключений."""
        for obj, method in [
            (self._browser, "close"),
            (self._playwright, "stop"),
        ]:
            if obj is not None:
                try:
                    await getattr(obj, method)()
                except Exception:
                    pass
        self._browser = None
        self._context = None
        self._playwright = None
        logger.info("Браузер остановлен.")


# Единственный экземпляр на весь процесс
browser_manager = BrowserManager()
