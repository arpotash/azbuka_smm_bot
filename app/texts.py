"""Все строки интерфейса и клавиатуры в одном месте."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class SlotCb(CallbackData, prefix="slot"):
    action: str  # publish | revise | skip
    slot_id: int


class SugCb(CallbackData, prefix="sug"):
    action: str  # pick | more
    slot_id: int
    idx: int


def reminder_keyboard(slot_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data=SlotCb(action="skip", slot_id=slot_id))
    return kb.as_markup()


def suggestions_keyboard(slot_id: int, idxs: list[int], allow_more: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx in idxs:
        kb.button(text=f"Взять {idx}", callback_data=SugCb(action="pick", slot_id=slot_id, idx=idx))
    rows = [len(idxs)] if idxs else []
    if allow_more:
        kb.button(text="Другие варианты", callback_data=SugCb(action="more", slot_id=slot_id, idx=0))
        rows.append(1)
    kb.button(text="Пропустить", callback_data=SlotCb(action="skip", slot_id=slot_id))
    rows.append(1)
    kb.adjust(*rows)
    return kb.as_markup()


def preview_keyboard(slot_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Опубликовать", callback_data=SlotCb(action="publish", slot_id=slot_id))
    kb.button(text="Правки", callback_data=SlotCb(action="revise", slot_id=slot_id))
    kb.button(text="Пропустить", callback_data=SlotCb(action="skip", slot_id=slot_id))
    kb.adjust(1, 2)
    return kb.as_markup()


# --- команды ---

START = (
    "Привет! Я помогаю вести канал.\n\n"
    "По расписанию я напоминаю о посте. В ответ пришлите материал: текст, фото, ссылку на товар. "
    "Я задам уточняющие вопросы, если чего-то не хватает, и покажу готовый пост с кнопками "
    "«Опубликовать», «Правки», «Пропустить».\n\n"
    "Можно не ждать напоминания: просто пришлите материал, и я открою пост.\n\n"
    "Команды:\n"
    "/status — что сейчас в работе\n"
    "/cancel — отменить текущий пост\n"
    "/help — эта справка"
)

HELP = START

ACCESS_DENIED = "Доступ ограничен. Ваш id: {user_id}. Передайте его администратору бота."
ADMIN_ONLY = "Эта команда только для администраторов."

NO_OPEN_SLOT = "Сейчас нет поста в работе. Пришлите материал, и я его открою."
STATUS_TEMPLATE = (
    "Пост #{slot_id} ({kind})\n"
    "Тема: {theme}\n"
    "Статус: {status}\n"
    "Ведёт: {manager}\n"
    "Материалов: {materials}\n"
    "Опубликовано всего: {posts}"
)
STATUS_NAMES = {
    "reminder": "ждём материал",
    "collecting": "собираю материал",
    "drafting": "черновик готов, жду решения",
    "revision": "жду правки",
    "published": "опубликован",
    "skipped": "пропущен",
}
KIND_NAMES = {"scheduled": "по расписанию", "manual": "вручную"}

CANCELLED = "Пост #{slot_id} отменён."
NEW_SLOT_CREATED = "Открыл пост #{slot_id} вручную и разослал напоминания."
NEW_SLOT_EXISTS = "На сегодня слот уже создавался, открыл ручной пост #{slot_id}."

LLMTEST_OK = "Модель отвечает. Ответ: {reply}\nЗапрос: {request_id}, токены: {usage}"
LLMTEST_FAIL = "Модель не отвечает: {error}"

# --- напоминания и слоты ---

REMINDER = (
    "Пора выпустить пост в канал.\n\n"
    "Пришлите материал одним или несколькими сообщениями: что за товар или событие, "
    "характеристики, ссылку на карточку товара, фото. "
    "Я соберу всё в пост и покажу превью.\n\n"
    "Если сегодня нечего публиковать, нажмите «Пропустить»."
)
SLOT_TAKEN = "Материал по посту #{slot_id} взял {name}."
SLOT_TAKEN_BY_OTHER = "Этот пост уже ведёт другой менеджер. Дождитесь публикации или отмены."
SLOT_OPENED_MANUAL = "Открыл новый пост #{slot_id}. Собираю материал…"
SLOT_SKIPPED = "Пост #{slot_id} пропущен."
SLOT_SKIPPED_NEW_SLOT = "Пост #{slot_id} не был закрыт и помечен как пропущенный: пришло новое напоминание."
SLOT_ALREADY_CLOSED = "Этот пост уже закрыт."
SEND_AS_PHOTO = "Пришлите изображение как фото, а не как файл."
UNSUPPORTED_CONTENT = "Я понимаю только текст и фото."

# --- превью и публикация ---

PREVIEW_ACTIONS = "Что делаем с постом?"
PREVIEW_NOTE_SPLIT = "Текст не влез в подпись к фото (лимит 1024 символа), в канале фото и текст уйдут отдельными сообщениями."
PREVIEW_NOTE_PLAIN = "Модель прислала невалидную разметку, публикую без форматирования."
REVISE_PROMPT = "Напишите, что изменить в посте. Можно добавить фото."
PUBLISHED = "Опубликовано: {link}"
PUBLISHED_NO_LINK = "Опубликовано."
PUBLISH_FORBIDDEN = "Бот не может писать в канал. Проверьте, что он добавлен администратором с правом публикации."
PUBLISH_FAILED = "Не удалось опубликовать: {error}"
PROCESSING_FAILED_GENERIC = "Не получилось обработать материал. Администратор уведомлён."

# --- ошибки LLM ---

LLM_RATE_LIMIT = "Сервис перегружен, попробуйте через минуту: пришлите любое сообщение, и я повторю."
LLM_API_ERROR = "Ошибка модели, администратор уведомлён. Пришлите любое сообщение, чтобы повторить."
LLM_CONNECTION = "Нет связи с моделью. Пришлите любое сообщение, и я повторю попытку."
LLM_REFUSAL = "Модель отказалась писать этот пост. Попробуйте переформулировать материал."
LLM_FORMAT = "Модель ответила в неожиданном формате. Пришлите любое сообщение, чтобы повторить."
ADMIN_LLM_ALERT = "Пост #{slot_id}: {count} ошибок модели подряд. Последняя: {error}"
ADMIN_REMINDER_FAILED = "Не смог отправить напоминание менеджеру {user_id}: он не начинал диалог с ботом (/start)."

# --- промпты-ретраи для модели ---

FIX_HTML_PROMPT = (
    "В твоём ответе невалидная Telegram-HTML-разметка: {reason}.\n"
    "Верни тот же пост целиком в <post>…</post>, используя только теги "
    "b, i, u, s, a href, code, pre, tg-spoiler, blockquote. Все теги должны быть закрыты, "
    "символы < > & в тексте экранируй как &lt; &gt; &amp;. Без markdown и пояснений."
)
SHORTEN_PROMPT = (
    "Пост длиннее лимита: {length} символов при лимите {limit}. "
    "Сократи до {target} символов, сохрани ссылку и ключевые характеристики. "
    "Верни полный пост в <post>…</post>."
)
EDIT_REQUEST_PREFIX = "Правки от менеджера: "
PHOTO_CAPTION_PREFIX = "Подпись к фото {n}: "
PHOTOS_ONLY_NOTE = "(Менеджер прислал фото без текста.)"
PREVIEW_STALE = "Это превью уже не актуально."
CHANNEL_ID_INFO = "Это пост из канала «{title}». Его id для настроек: {chat_id}"

# --- темы и подсказки ---

REMINDER_THEMED = "Пора выпустить пост в канал.\n\nТема сегодня: <b>{title}</b>.\n{hint}"
REMINDER_FOOTER = "\nЕсли сегодня публиковать нечего, нажмите «Пропустить»."
SUGGESTIONS_HEADER = "\nВарианты от бота, нажмите «Взять N» или пришлите свой материал:"
SUGGESTION_ITEM = "{idx}. <b>{title}</b>\n{line}\n{url}"
SUGGESTIONS_UNAVAILABLE = "\nСайт сейчас не отвечает, варианты подобрать не удалось. Пришлите свой материал."
SUGGESTIONS_NONE_LEFT = "Новых вариантов не нашлось. Пришлите свой материал."
PROMO_NONE = "\nАкций на сайте сейчас нет."
SUGGESTION_TAKEN = "Беру: <b>{title}</b>. Готовлю пост, можно добавить свой текст или фото."
SUGGESTION_TAKEN_OTHERS = "Пост #{slot_id}: {name} выбрал вариант «{title}»."
SUGGESTION_NOT_FOUND = "Этот вариант уже недоступен."
SUGGESTION_FETCH_FAILED = "Не удалось загрузить материал с сайта. Пришлите его текстом или выберите другой вариант."
MORE_VARIANTS_TITLE = "Ещё варианты:"
NEW_SLOT_THEME_UNKNOWN = "Неизвестная тема «{theme}». Доступны: {themes}."
ADMIN_SUGGEST_FAILED = "Пост #{slot_id}: не удалось подобрать варианты с сайта: {error}"

MATERIAL_PRODUCT_HEADER = "Карточка товара с сайта as-lesa.ru"
MATERIAL_PROMO_HEADER = "Карточка акционного товара с сайта as-lesa.ru"
MATERIAL_SOURCE_HEADER = "Источник: {source}"
MATERIAL_BLOG_HEADER = "Источник: статья из блога as-lesa.ru"
