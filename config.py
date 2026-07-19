import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise ValueError(f"Обязательная переменная окружения не задана: {key}")
    return val


BOT_TOKEN: str = _require("BOT_TOKEN")
GEMINI_API_KEY: str = _require("GEMINI_API_KEY")
CS_EMAIL: str = _require("CS_EMAIL")
CS_PASSWORD: str = _require("CS_PASSWORD")
CS_URL: str = _require("CS_URL").rstrip("/")

SHOP_ADDRESS: str = os.getenv("SHOP_ADDRESS", "")
SHOP_PHONE: str = os.getenv("SHOP_PHONE", "")
SHOP_TELEGRAM: str = os.getenv("SHOP_TELEGRAM", "")
EXCEL_CATALOG_PATH: str = os.getenv("EXCEL_CATALOG_PATH", "catalog.xlsx")

ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]
