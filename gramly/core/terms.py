"""Terminology and constants for Gramly.

The central entity is deliberately NOT called "document". Research on contract
terminology (WIPO glossary, Vienna Convention wording) shows "договор/документ"
carries a heavy juridical-official connotation. What Gramly records is a promise
between Telegram parties that is auto-verified by a bot -- closer to a *pact*:
"a kind of codified promise" parties agree to conform to (LAWS.com).

So: Пакт (pact) is the entity, подписи (signatures) are the acts,
порядочность (decency) is the reputation metric, гарант (guarantor) vouches.
"""

APP_NAME = "Gramly"
APP_TAGLINE = "Пакты о порядочности и верности"
APP_EMOJI = "🤝"

ENTITY = "Пакт"
ENTITY_PLURAL = "Пакты"
ENTITY_GENITIVE = "пакта"

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
RATING_MAX = 100.0

# Rating tiers by score.
TIERS = [
    (95, "Легенда", "🏅"),
    (85, "Образцовый", "💎"),
    (70, "Надёжный", "🛡"),
    (50, "Проверенный", "✔️"),
    (30, "Новичок", "🌱"),
    (0, "Непроверенный", "⚪"),
]

# Free guarantor case quota per calendar month; past it both sides pay Stars.
GUARANTOR_FREE_CASES = 3
STAR_FEE_PER_SIDE = 10

WATCH_REFRESH_SECONDS = 900