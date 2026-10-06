"""Terminology and constants for Gramly.

The central entity is presented in the client as a short, neutral
"договорённость". Internal storage names remain stable for compatibility.
Порядочность is the reputation metric, гарант is the optional dispute role.
"""

APP_NAME = "Gramly"
APP_TAGLINE = "Договорённости, порядочность и гарантии"
APP_EMOJI = "🤝"

ENTITY = "Договорённость"
ENTITY_PLURAL = "Договорённости"
ENTITY_GENITIVE = "договорённости"

SIGN = "Подписан"
SIGNED_BOTH = "Подписан обеими сторонами"
AWAIT = "Ожидает подписи"
ACTIVE = "Действует"
PAUSED = "Приостановлен"
DONE = "Исполнен"
BROKEN = "Нарушен"
CANCELLED = "Расторгнут"
DISPUTE = "Спор"

STATUSES = {
    "draft": AWAIT,
    "half": AWAIT,
    "signed": SIGNED_BOTH,
    "active": ACTIVE,
    "paused": PAUSED,
    "done": DONE,
    "broken": BROKEN,
    "cancelled": CANCELLED,
    "dispute": DISPUTE,
}

STATUS_EMOJI = {
    "draft": "⏳",
    "half": "⏳",
    "signed": "✅",
    "active": "🟢",
    "paused": "🟠",
    "done": "🏁",
    "broken": "⛔",
    "cancelled": "🚫",
    "dispute": "⚖️",
}

# kind -> (emoji, noun, preposition phrase, default conditions hints)
PACT_KINDS = {
    "partner": {
        "emoji": "🤝",
        "label": "Партнёрский",
        "about": "партнёрства",
        "hint": "Пиар, совместные активности, обмен аудиториями",
    },
    "pr": {
        "emoji": "📣",
        "label": "Продвижение",
        "about": "взаимного пиара",
        "hint": "Рекламные размещения, репосты, совместные посты",
    },
    "exchange": {
        "emoji": "🔁",
        "label": "Обмен",
        "about": "обмена",
        "hint": "Обмен аккаунтами, доступами, подписками",
    },
    "service": {
        "emoji": "🛠",
        "label": "Услуга",
        "about": "оказания услуги",
        "hint": "Дизайн, код, генерация, настройка, работа под ключ",
    },
    "other": {
        "emoji": "📌",
        "label": "Иное",
        "about": "взаимных обязательств",
        "hint": "Свободная формулировка обязательств",
    },
}
DEFAULT_KIND = "other"

KIND_ORDER = ["partner", "pr", "exchange", "service", "other"]

RATING_BASE = 50.0
RATING_MIN = 0.0
RATING_MAX = float("inf")

# Rating tiers by score.
TIERS = [
    (10000, "Легендарный", ""),
    (1000, "Очень сильный", ""),
    (100, "Надёжный", ""),
    (50, "Стабильный", ""),
    (1, "Начальный", ""),
    (0, "Новый", ""),
]

# Free guarantor case quota per calendar month; past it both sides pay Stars.
GUARANTOR_FREE_CASES = 3
STAR_FEE_PER_SIDE = 10

WATCH_REFRESH_SECONDS = 900