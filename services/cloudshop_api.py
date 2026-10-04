"""
CloudShop API — прямые HTTP-запросы вместо Playwright.

Авторизация через POST /auth, данные через GET /proxy.
Каталог содержит name, price, stock, total_stock — всё что нужно.
"""

import asyncio
import logging
import re
import time
from urllib.parse import quote

import httpx

from config import CS_URL, CS_EMAIL, CS_PASSWORD

logger = logging.getLogger(__name__)

# Базовый URL API — без /pos/home
_API_BASE = CS_URL.split("/pos")[0] if "/pos" in CS_URL else CS_URL

# ID магазина из ответа API (store._id)
_STORE_ID = "CS_STORE_ID"
# Часовой пояс (UTC+5 = 18000 секунд — как в браузере)
_TIMEZONE = 18000

# Пагинация каталога
_CATALOG_LIMIT = 1000

# Заголовки — nginx роутит по user-agent, без него запрос падает в Express (500)
_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "api": "v3",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "origin": _API_BASE,
    "referer": f"{_API_BASE}/pos/home",
}


class CloudShopAPI:
    """HTTP-клиент для CloudShop POS API."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._logged_in = False
        self._last_login: float = 0.0
        self._client_id: str | None = None
        # TTL сессии — 25 минут (сессия живёт ~30 мин)
        self._session_ttl = 25 * 60

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._client

    async def _ensure_session(self) -> None:
        """Логинится если сессия истекла или отсутствует."""
        now = time.monotonic()
        if self._logged_in and (now - self._last_login) < self._session_ttl:
            return
        await self._login()

    async def _login(self) -> None:
        """POST /auth — получает cookies для дальнейших запросов."""
        client = await self._ensure_client()
        url = f"{_API_BASE}/auth"
        resp = await client.post(url, json={
            "login": CS_EMAIL,
            "password": CS_PASSWORD,
        }, headers={**_HEADERS, "referer": f"{_API_BASE}/pos/login"})
        if resp.status_code != 200:
            logger.error(
                f"CloudShop auth: HTTP {resp.status_code}, body={resp.text[:200]}"
            )
            raise RuntimeError(f"CloudShop auth HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("status"):
            raise RuntimeError(f"CloudShop auth failed: {data}")
        self._logged_in = True
        self._last_login = time.monotonic()
        # Сохраняем client_id из ответа
        if data.get("data"):
            self._client_id = data["data"][0].get("company_id")
            if self._client_id:
                logger.info(f"CloudShop API: авторизация выполнена, client_id={self._client_id}")
            else:
                logger.warning("CloudShop API: client_id не найден в ответе auth")
        else:
            logger.warning("CloudShop API: auth не вернул data")

    async def _get(self, path: str) -> dict:
        """GET /proxy?path=... — авторизованный запрос к API."""
        await self._ensure_session()
        client = await self._ensure_client()
        encoded_path = quote(path, safe="")
        url = f"{_API_BASE}/proxy?path={encoded_path}&timezone={_TIMEZONE}"
        resp = await client.get(url, headers=_HEADERS)
        # Если 401 — перелогиниваемся и пробуем снова
        if resp.status_code == 401:
            logger.warning("CloudShop API: сессия истекла, перелогиниваюсь...")
            self._logged_in = False
            await self._ensure_session()
            resp = await client.get(url, headers=_HEADERS)
        if resp.status_code != 200:
            logger.error(
                f"CloudShop API: HTTP {resp.status_code} для {path}, "
                f"body={resp.text[:200]}"
            )
        resp.raise_for_status()
        return resp.json()

    async def fetch_catalog(self) -> list[dict]:
        """
        Загружает полный каталог товаров.
        Возвращает список словарей с ключами:
          name, price, stock, total_stock, _id, code, type
        """
        await self._ensure_session()
        if not self._client_id:
            raise RuntimeError("client_id неизвестен — выполните авторизацию")
        all_items: list[dict] = []
        offset = 0
        while True:
            path = (
                f"/data/{self._client_id}/catalog"
                f"?offset={offset}&limit={_CATALOG_LIMIT}"
            )
            data = await self._get(path)
            items = data.get("data", [])
            all_items.extend(items)
            total = data.get("total", 0)
            logger.debug(
                f"Каталог: загружено {len(items)} (offset={offset}, всего {total})"
            )
            if len(all_items) >= total or not items:
                break
            offset += _CATALOG_LIMIT
        logger.info(f"CloudShop API: каталог загружен — {len(all_items)} товаров")
        return all_items

    async def search(self, query: str) -> list[dict]:
        """
        Ищет товары по названию.
        Возвращает список {name, price, qty, total_stock}.
        """
        catalog = await self.fetch_catalog()
        query_lower = query.lower().strip()
        results = []
        for item in catalog:
            name = item.get("name", "")
            if query_lower in name.lower():
                stock = item.get("stock", {})
                qty = stock.get(_STORE_ID, 0)
                results.append({
                    "name": name,
                    "qty": qty,
                    "price": str(item.get("price", "")),
                    "total_stock": item.get("total_stock", 0),
                })
        return results

    async def search_multiple(
        self, game_names: list[str]
    ) -> dict[str, list[dict]]:
        """
        Ищет несколько товаров за один загрузку каталога.
        Возвращает {game_name: [{name, qty, price, total_stock}]}.
        """
        if not game_names:
            return {}

        catalog = await self.fetch_catalog()
        store_id = _STORE_ID

        # Индексируем каталог по нижнему регистру имени
        catalog_index: list[tuple[str, dict]] = [
            (item.get("name", "").lower(), item) for item in catalog
        ]

        output: dict[str, list[dict]] = {}
        for game_name in game_names:
            clean = re.sub(r",?\s*арт\..*$", "", game_name, flags=re.IGNORECASE).strip()
            clean = re.sub(r"^Настольная\s+игра\s*:\s*", "", clean, flags=re.IGNORECASE).strip()
            clean = re.sub(r"^Настольная\s+игра\s+", "", clean, flags=re.IGNORECASE).strip()
            clean = clean.strip('«»"\'') or game_name
            query_lower = clean.lower().strip()
            matches = []
            for name_lower, item in catalog_index:
                if query_lower in name_lower:
                    stock = item.get("stock", {})
                    qty = stock.get(store_id, 0)
                    matches.append({
                        "name": item.get("name", ""),
                        "qty": qty,
                        "price": str(item.get("price", "")),
                        "total_stock": item.get("total_stock", 0),
                    })
            output[game_name] = matches
            logger.info(
                f"Поиск '{game_name}': {len(matches)} совпадений "
                f"из {len(catalog)} товаров"
            )

        return output

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Глобальный экземпляр
cloudshop_api = CloudShopAPI()
