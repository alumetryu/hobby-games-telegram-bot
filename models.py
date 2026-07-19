from dataclasses import dataclass, field


@dataclass
class Game:
    """Игра из Excel-каталога."""
    name: str
    genre: str = ""
    age: str = ""
    players: str = ""
    tags: str = ""
    description: str = ""


@dataclass
class StockItem:
    """Позиция с сайта CloudShop."""
    name: str
    qty: int
    price: str
