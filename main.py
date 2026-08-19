import os
import json
import asyncio
import traceback
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()

TIMEZONE = os.getenv("TIMEZONE", "Asia/Vladivostok").strip()
DAILY_SEND_TIME = os.getenv("DAILY_SEND_TIME", "10:30").strip()
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "10000"))

CLIENTS_PER_DAY = int(os.getenv("CLIENTS_PER_DAY", "3"))

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

TZ = ZoneInfo(TIMEZONE)

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


# =========================================================
# BUSINESS RULES
# =========================================================

CONTACT_INTERVALS = {
    "Регулярный": 14,
    "Стабильный": 21,
    "Нерегулярный": 30,
}

# При одинаковой просрочке клиенты группы A выше B, B выше C.
GROUP_PRIORITY = {
    "A": 3,
    "А": 3,
    "B": 2,
    "В": 2,
    "C": 1,
    "С": 1,
}

RESULT_OPTIONS = [
    ("new_request", "🟢 Есть новый запрос"),
    ("planned", "📦 Планируются поставки"),
    ("no_shipments", "⏸ Пока поставок нет"),
    ("season", "📅 Пауза / сезонность"),
    ("competitor", "⚔️ Работает с другим подрядчиком"),
    ("no_answer", "📵 Не дозвонился"),
    ("other", "✏️ Другое"),
]

NEXT_CONTACT_OPTIONS = [
    (7, "7 дней"),
    (14, "14 дней"),
    (30, "30 дней"),
    (60, "60 дней"),
]


# =========================================================
# FSM
# =========================================================

class ContactFlow(StatesGroup):
    waiting_comment = State()
    waiting_custom_date = State()
    waiting_postpone_reason = State()
    waiting_postpone_date = State()


# =========================================================
# GOOGLE SHEETS
# =========================================================

CLIENT_HEADERS = [
    "client",
    "manager",
    "category",
    "group",
    "last_order",
    "last_request",
    "last_contact",
    "next_contact",
    "last_result",
    "active",
]

MANAGER_HEADERS = [
    "manager",
    "telegram_id",
    "active",
]

TASK_HEADERS = [
    "task_date",
    "manager",
    "telegram_id",
    "client",
    "status",
    "created_at",
    "completed_at",
]

COMM_HEADERS = [
    "date",
    "manager",
    "telegram_id",
    "client",
    "result",
    "comment",
    "next_contact",
    "source",
    "created_at",
]


def get_spreadsheet():
    if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_SHEET_ID:
        raise RuntimeError(
            "Не заданы GOOGLE_CREDENTIALS_JSON или GOOGLE_SHEET_ID"
        )

    info = json.loads(GOOGLE_CREDENTIALS_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)

    return gc.open_by_key(GOOGLE_SHEET_ID)


def ensure_headers(ws, required_headers):
    values = ws.get_all_values()

    if not values:
        ws.append_row(required_headers, value_input_option="USER_ENTERED")
        return required_headers

    headers = values[0][:]
    changed = False

    for header in required_headers:
        if header not in headers:
            headers.append(header)
            changed = True

    if changed:
        end = gspread.utils.rowcol_to_a1(1, len(headers))
        ws.update(f"A1:{end}", [headers])

    return headers


def get_or_create_ws(title: str, headers: list[str]):
    ss = get_spreadsheet()

    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(
            title=title,
            rows=3000,
            cols=max(12, len(headers)),
        )

    ensure_headers(ws, headers)
    return ws


def clients_ws():
    return get_or_create_ws("CLIENTS", CLIENT_HEADERS)


def managers_ws():
    return get_or_create_ws("MANAGERS", MANAGER_HEADERS)


def tasks_ws():
    return get_or_create_ws("DAILY_TASKS", TASK_HEADERS)


def communications_ws():
    return get_or_create_ws("COMMUNICATIONS", COMM_HEADERS)


def get_headers(ws):
    values = ws.get_all_values()
    return values[0] if values else []


def row_to_dict(headers, values):
    padded = values + [""] * max(0, len(headers) - len(values))
    return dict(zip(headers, padded))


def update_row_by_fields(ws, row_num: int, updates: dict):
    headers = ensure_headers(ws, list(updates.keys()))
    row = ws.row_values(row_num)

    if len(row) < len(headers):
        row += [""] * (len(headers) - len(row))

    for field, value in updates.items():
        col_idx = headers.index(field)
        row[col_idx] = "" if value is None else str(value)

    end = gspread.utils.rowcol_to_a1(row_num, len(headers))
    ws.update(f"A{row_num}:{end}", [row])


# =========================================================
# HELPERS
# =========================================================

def today_local() -> date:
    return datetime.now(TZ).date()


def now_local() -> datetime:
    return datetime.now(TZ)


def normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split()).strip()


def parse_date(value):
    if value is None:
        return None

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = normalize_text(value)

    if not text or text.lower() in {"nan", "nat", "none"}:
        return None

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)

    if pd.isna(parsed):
        return None

    return parsed.date()


def fmt_date(value):
    parsed = parse_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else ""


def pretty_date(value):
    parsed = parse_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else "—"


def normalize_category(value):
    text = normalize_text(value).lower()

    if "регуляр" in text:
        return "Регулярный"

    if "стабил" in text:
        return "Стабильный"

    if "нерегуляр" in text:
        return "Нерегулярный"

    return normalize_text(value) or "Нерегулярный"


def normalize_group(value):
    value = normalize_text(value).upper()

    # Сохраняем как в исходном файле, но убираем лишние пробелы.
    return value


def find_column(df: pd.DataFrame, aliases: list[str]):
    normalized = {
        normalize_text(column).lower(): column
        for column in df.columns
    }

    # Сначала только точное совпадение.
    for alias in aliases:
        key = normalize_text(alias).lower()
        if key in normalized:
            return normalized[key]

    return None


def client_key(client: str, manager: str):
    return (
        normalize_text(manager).lower(),
        normalize_text(client).lower(),
    )


# =========================================================
# PORTFOLIO IMPORT
# =========================================================

def import_portfolio_excel(path: str):
    df = pd.read_excel(path)

    # ТОЧНЫЕ НАЗВАНИЯ ИЗ ВАШЕГО ФАЙЛА
    client_col = find_column(
        df,
        [
            "Наименование",
            "Клиент",
            "Компания",
        ],
    )

    manager_col = find_column(
        df,
        [
            "Оперативный менеджер",
            "Опер. менеджер",
            "Опер менеджер",
        ],
    )

    category_col = find_column(
        df,
        [
            "Признак",
            "Категория",
        ],
    )

    group_col = find_column(
        df,
        [
            "Группа",
        ],
    )

    last_order_col = find_column(
        df,
        [
            "Дата последнего заказа",
            "Последний заказ",
        ],
    )

    last_request_col = find_column(
        df,
        [
            "Дата последнего запроса",
            "Последний запрос",
        ],
    )

    missing = []

    if not client_col:
        missing.append("Наименование")

    if not manager_col:
        missing.append("Оперативный менеджер")

    if not category_col:
        missing.append("Признак")

    if not group_col:
        missing.append("Группа")

    if not last_order_col:
        missing.append("Дата последнего заказа")

    if not last_request_col:
        missing.append("Дата последнего запроса")

    if missing:
        raise ValueError(
            "Не найдены колонки: " + ", ".join(missing)
        )

    ws = clients_ws()
    headers = get_headers(ws)
    raw_rows = ws.get_all_values()[1:]

    existing = {}

    for row_num, values in enumerate(raw_rows, start=2):
        row = row_to_dict(headers, values)

        client = normalize_text(row.get("client"))
        manager = normalize_text(row.get("manager"))

        if client and manager:
            existing[client_key(client, manager)] = (
                row_num,
                row,
            )

    new_count = 0
    updated_count = 0
    skipped_count = 0

    for _, source_row in df.iterrows():
        client = normalize_text(source_row.get(client_col))
        manager = normalize_text(source_row.get(manager_col))

        if not client or not manager:
            skipped_count += 1
            continue

        category = normalize_category(
            source_row.get(category_col)
        )

        group_value = normalize_group(
            source_row.get(group_col)
        )

        last_order = fmt_date(
            source_row.get(last_order_col)
        )

        last_request = fmt_date(
            source_row.get(last_request_col)
        )

        key = client_key(client, manager)

        if key in existing:
            row_num, old = existing[key]

            update_row_by_fields(
                ws,
                row_num,
                {
                    "client": client,
                    "manager": manager,
                    "category": category,
                    "group": group_value,
                    "last_order": last_order,
                    "last_request": last_request,
                    # last_contact / next_contact / last_result НЕ затираем
                    "active": "1",
                },
            )

            updated_count += 1

        else:
            headers = ensure_headers(ws, CLIENT_HEADERS)

            data = {
                "client": client,
                "manager": manager,
                "category": category,
                "group": group_value,
                "last_order": last_order,
                "last_request": last_request,
                "last_contact": "",
                "next_contact": "",
                "last_result": "",
                "active": "1",
            }

            ws.append_row(
                [data.get(header, "") for header in headers],
                value_input_option="USER_ENTERED",
            )

            new_count += 1

    return {
        "new": new_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "source_rows": len(df),
    }


# =========================================================
# MANAGERS
# =========================================================

def register_manager(manager: str, telegram_id: int):
    ws = managers_ws()
    headers = get_headers(ws)
    rows = ws.get_all_values()[1:]

    manager_norm = normalize_text(manager).lower()

    for row_num, values in enumerate(rows, start=2):
        row = row_to_dict(headers, values)

        if normalize_text(row.get("manager")).lower() == manager_norm:
            update_row_by_fields(
                ws,
                row_num,
                {
                    "manager": manager,
                    "telegram_id": str(telegram_id),
                    "active": "1",
                },
            )
            return

    headers = ensure_headers(ws, MANAGER_HEADERS)

    data = {
        "manager": manager,
        "telegram_id": str(telegram_id),
        "active": "1",
    }

    ws.append_row(
        [data.get(h, "") for h in headers],
        value_input_option="USER_ENTERED",
    )


def get_manager_by_telegram_id(telegram_id: int):
    ws = managers_ws()
    headers = get_headers(ws)

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(headers, values)

        if (
            str(row.get("telegram_id", "")).strip()
            == str(telegram_id)
            and str(row.get("active", "1")).strip() != "0"
        ):
            return normalize_text(row.get("manager"))

    return None


def get_active_managers():
    ws = managers_ws()
    headers = get_headers(ws)
    result = []

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(headers, values)

        manager = normalize_text(row.get("manager"))
        telegram_id = str(row.get("telegram_id", "")).strip()
        active = str(row.get("active", "1")).strip()

        if manager and telegram_id and active != "0":
            result.append(
                {
                    "manager": manager,
                    "telegram_id": int(telegram_id),
                }
            )

    return result


# =========================================================
# CLIENT STATE
# =========================================================

def get_client_state(client: str, manager: str):
    ws = clients_ws()
    headers = get_headers(ws)

    for row_num, values in enumerate(
        ws.get_all_values()[1:],
        start=2,
    ):
        row = row_to_dict(headers, values)

        if client_key(
            row.get("client", ""),
            row.get("manager", ""),
        ) == client_key(client, manager):
            row["_row_num"] = row_num
            return row

    return {}


def update_client_state(
    client: str,
    manager: str,
    last_contact: str | None = None,
    next_contact: str | None = None,
    last_result: str | None = None,
):
    state = get_client_state(client, manager)

    if not state:
        return

    updates = {}

    if last_contact is not None:
        updates["last_contact"] = last_contact

    if next_contact is not None:
        updates["next_contact"] = next_contact

    if last_result is not None:
        updates["last_result"] = last_result

    if updates:
        update_row_by_fields(
            clients_ws(),
            state["_row_num"],
            updates,
        )


# =========================================================
# DAILY TASKS
# =========================================================

def get_today_tasks(manager: str):
    ws = tasks_ws()
    headers = get_headers(ws)

    today_str = today_local().strftime("%Y-%m-%d")
    result = []

    for row_num, values in enumerate(
        ws.get_all_values()[1:],
        start=2,
    ):
        row = row_to_dict(headers, values)

        if (
            str(row.get("task_date", "")).strip() == today_str
            and normalize_text(row.get("manager")).lower()
            == normalize_text(manager).lower()
        ):
            row["_row_num"] = row_num
            result.append(row)

    return result


def get_or_create_daily_tasks(manager: str, telegram_id: int):
    existing = get_today_tasks(manager)

    if existing:
        return existing

    today = today_local()

    cws = clients_ws()
    headers = get_headers(cws)

    candidates = []

    for values in cws.get_all_values()[1:]:
        row = row_to_dict(headers, values)

        if str(row.get("active", "1")).strip() == "0":
            continue

        row_manager = normalize_text(row.get("manager"))

        if row_manager.lower() != normalize_text(manager).lower():
            continue

        client = normalize_text(row.get("client"))

        if not client:
            continue

        category = normalize_category(row.get("category"))
        group_value = normalize_group(row.get("group"))

        interval = CONTACT_INTERVALS.get(category, 30)

        last_contact = parse_date(row.get("last_contact"))
        next_contact = parse_date(row.get("next_contact"))
        last_order = parse_date(row.get("last_order"))
        last_request = parse_date(row.get("last_request"))

        # Если явно отложили на будущую дату — не показываем.
        if next_contact and next_contact > today:
            continue

        if next_contact and next_contact <= today:
            due_date = next_contact
        elif last_contact:
            due_date = last_contact + timedelta(days=interval)
        else:
            anchors = [
                d for d in (last_order, last_request)
                if d is not None
            ]

            # Если контакта через бота еще не было,
            # считаем от последней коммерческой активности.
            if anchors:
                due_date = max(anchors) + timedelta(days=interval)
            else:
                due_date = date(2000, 1, 1)

        if due_date > today:
            continue

        overdue_days = (today - due_date).days

        category_rank = {
            "Регулярный": 3,
            "Стабильный": 2,
            "Нерегулярный": 1,
        }.get(category, 0)

        group_rank = GROUP_PRIORITY.get(group_value, 0)

        candidates.append(
            {
                "client": client,
                "manager": manager,
                "category": category,
                "group": group_value,
                "overdue_days": overdue_days,
                "category_rank": category_rank,
                "group_rank": group_rank,
            }
        )

    # 1) насколько просрочен контакт
    # 2) группа A/B/C
    # 3) регулярность
    candidates.sort(
        key=lambda x: (
            -x["overdue_days"],
            -x["group_rank"],
            -x["category_rank"],
            x["client"].lower(),
        )
    )

    selected = candidates[:CLIENTS_PER_DAY]

    tws = tasks_ws()
    task_headers = ensure_headers(tws, TASK_HEADERS)
    today_str = today.strftime("%Y-%m-%d")
    created_at = now_local().strftime("%d.%m.%Y %H:%M")

    created_tasks = []

    for item in selected:
        data = {
            "task_date": today_str,
            "manager": manager,
            "telegram_id": str(telegram_id),
            "client": item["client"],
            "status": "new",
            "created_at": created_at,
            "completed_at": "",
        }

        tws.append_row(
            [data.get(h, "") for h in task_headers],
            value_input_option="USER_ENTERED",
        )

    # Перечитываем, чтобы получить row_num.
    return get_today_tasks(manager)


def get_task_by_row(task_row: int):
    ws = tasks_ws()
    headers = get_headers(ws)

    values = ws.row_values(task_row)

    if not values:
        return None

    row = row_to_dict(headers, values)
    row["_row_num"] = task_row
    return row


def mark_task_status(task_row: int, status: str):
    ws = tasks_ws()

    completed_at = (
        now_local().strftime("%d.%m.%Y %H:%M")
        if status in {"done", "postponed"}
        else ""
    )

    update_row_by_fields(
        ws,
        task_row,
        {
            "status": status,
            "completed_at": completed_at,
        },
    )


# =========================================================
# COMMUNICATIONS
# =========================================================

def save_communication(
    client: str,
    manager: str,
    telegram_id: int,
    result: str,
    comment: str,
    next_contact: date | None,
    task_row: int,
):
    contact_date = today_local()

    next_text = (
        next_contact.strftime("%d.%m.%Y")
        if next_contact
        else ""
    )

    ws = communications_ws()
    headers = ensure_headers(ws, COMM_HEADERS)

    data = {
        "date": contact_date.strftime("%d.%m.%Y"),
        "manager": manager,
        "telegram_id": str(telegram_id),
        "client": client,
        "result": result,
        "comment": comment,
        "next_contact": next_text,
        "source": "telegram",
        "created_at": now_local().strftime("%d.%m.%Y %H:%M"),
    }

    ws.append_row(
        [data.get(h, "") for h in headers],
        value_input_option="USER_ENTERED",
    )

    update_client_state(
        client=client,
        manager=manager,
        last_contact=contact_date.strftime("%d.%m.%Y"),
        next_contact=next_text,
        last_result=result,
    )

    mark_task_status(task_row, "done")


def save_postpone(
    client: str,
    manager: str,
    telegram_id: int,
    reason: str,
    next_contact: date,
    task_row: int,
):
    next_text = next_contact.strftime("%d.%m.%Y")

    update_client_state(
        client=client,
        manager=manager,
        next_contact=next_text,
        last_result=f"Отложено: {reason}",
    )

    mark_task_status(task_row, "postponed")

    ws = communications_ws()
    headers = ensure_headers(ws, COMM_HEADERS)

    data = {
        "date": today_local().strftime("%d.%m.%Y"),
        "manager": manager,
        "telegram_id": str(telegram_id),
        "client": client,
        "result": "Отложено",
        "comment": reason,
        "next_contact": next_text,
        "source": "telegram",
        "created_at": now_local().strftime("%d.%m.%Y %H:%M"),
    }

    ws.append_row(
        [data.get(h, "") for h in headers],
        value_input_option="USER_ENTERED",
    )


# =========================================================
# KEYBOARDS
# =========================================================

def client_actions_keyboard(task_row: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Связался",
                    callback_data=f"contact:{task_row}",
                ),
                InlineKeyboardButton(
                    text="⏰ Отложить",
                    callback_data=f"postpone:{task_row}",
                ),
            ]
        ]
    )


def result_keyboard(task_row: int):
    rows = []

    for code, title in RESULT_OPTIONS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"result:{code}:{task_row}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_contact_keyboard(task_row: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="7 дней",
                    callback_data=f"next:7:{task_row}",
                ),
                InlineKeyboardButton(
                    text="14 дней",
                    callback_data=f"next:14:{task_row}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="30 дней",
                    callback_data=f"next:30:{task_row}",
                ),
                InlineKeyboardButton(
                    text="60 дней",
                    callback_data=f"next:60:{task_row}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Выбрать дату",
                    callback_data=f"next_custom:{task_row}",
                )
            ],
        ]
    )


def postpone_keyboard(task_row: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="7 дней",
                    callback_data=f"postpone_days:7:{task_row}",
                ),
                InlineKeyboardButton(
                    text="14 дней",
                    callback_data=f"postpone_days:14:{task_row}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="30 дней",
                    callback_data=f"postpone_days:30:{task_row}",
                ),
                InlineKeyboardButton(
                    text="60 дней",
                    callback_data=f"postpone_days:60:{task_row}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Выбрать дату",
                    callback_data=f"postpone_custom:{task_row}",
                )
            ],
        ]
    )


# =========================================================
# SEND TODAY TASKS
# =========================================================

async def send_today_tasks(telegram_id: int, manager: str):
    tasks = get_or_create_daily_tasks(
        manager,
        telegram_id,
    )

    active_tasks = [
        task
        for task in tasks
        if str(task.get("status", "")).strip()
        not in {"done", "postponed"}
    ]

    if not active_tasks:
        await bot.send_message(
            telegram_id,
            "✅ На сегодня клиентов для обязательного контакта нет.",
        )
        return

    await bot.send_message(
        telegram_id,
        (
            "📋 <b>Клиенты на сегодня</b>\n\n"
            f"Менеджер: <b>{manager}</b>\n"
            f"Количество: <b>{len(active_tasks)}</b>"
        ),
    )

    for index, task in enumerate(active_tasks, start=1):
        client = normalize_text(task.get("client"))
        task_row = int(task["_row_num"])

        state = get_client_state(
            client,
            manager,
        )

        category = normalize_category(
            state.get("category")
        )

        group_value = normalize_group(
            state.get("group")
        )

        last_contact = pretty_date(
            state.get("last_contact")
        )

        last_order = pretty_date(
            state.get("last_order")
        )

        last_request = pretty_date(
            state.get("last_request")
        )

        text = (
            f"<b>{index}. {client}</b>\n"
            f"Группа: <b>{group_value or '—'}</b>\n"
            f"Признак: <b>{category}</b>\n"
            f"Последний контакт: <b>{last_contact}</b>\n"
            f"Последний заказ: {last_order}\n"
            f"Последний запрос: {last_request}"
        )

        await bot.send_message(
            telegram_id,
            text,
            reply_markup=client_actions_keyboard(task_row),
        )


# =========================================================
# COMMANDS
# =========================================================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 <b>Внимание на клиента</b>\n\n"
        "Новая функция: контроль коммуникации.\n\n"
        "Команды:\n"
        "/register ФИО — привязать Telegram к менеджеру\n"
        "/today — получить 3 клиента на сегодня\n"
        "/status — проверить привязку\n"
        "/report — отчет руководителю\n\n"
        "Руководитель может отправить Excel с клиентским портфелем "
        "прямо в бот."
    )


@dp.message(Command("register"))
async def register(message: Message):
    manager = (
        message.text
        .replace("/register", "", 1)
        .strip()
    )

    if not manager:
        await message.answer(
            "Укажи ФИО менеджера.\n\n"
            "Например:\n"
            "<code>/register Лилия Буглак</code>"
        )
        return

    register_manager(
        manager,
        message.from_user.id,
    )

    await message.answer(
        "✅ Telegram привязан к менеджеру:\n"
        f"<b>{manager}</b>"
    )


@dp.message(Command("status"))
async def status(message: Message):
    manager = get_manager_by_telegram_id(
        message.from_user.id
    )

    if manager:
        await message.answer(
            "✅ Вы зарегистрированы как:\n"
            f"<b>{manager}</b>"
        )
    else:
        await message.answer(
            "⚠️ Вы еще не зарегистрированы.\n"
            "Используйте:\n"
            "<code>/register ФИО</code>"
        )


@dp.message(Command("today"))
async def today_command(message: Message):
    manager = get_manager_by_telegram_id(
        message.from_user.id
    )

    if not manager:
        await message.answer(
            "Сначала зарегистрируйтесь:\n"
            "<code>/register ФИО</code>"
        )
        return

    await send_today_tasks(
        message.from_user.id,
        manager,
    )


@dp.message(Command("report"))
async def report_command(message: Message):
    if (
        REPORT_CHAT_ID
        and str(message.chat.id) != str(REPORT_CHAT_ID)
    ):
        await message.answer(
            "Команда доступна только руководителю."
        )
        return

    await send_manager_report(
        message.chat.id
    )


# =========================================================
# PORTFOLIO UPLOAD
# =========================================================

@dp.message(F.document)
async def portfolio_upload(message: Message):
    filename = (
        message.document.file_name
        or "portfolio.xlsx"
    )

    if not filename.lower().endswith(
        (".xlsx", ".xls")
    ):
        await message.answer(
            "Нужен Excel-файл .xlsx или .xls"
        )
        return

    tmp = Path("/tmp") / (
        f"{message.from_user.id}_"
        f"{Path(filename).name}"
    )

    try:
        await bot.download(
            message.document,
            destination=tmp,
        )

        result = import_portfolio_excel(
            str(tmp)
        )

        await message.answer(
            "✅ <b>Портфель обновлен</b>\n\n"
            f"Новых клиентов: <b>{result['new']}</b>\n"
            f"Обновлено: <b>{result['updated']}</b>\n"
            f"Пропущено пустых строк: <b>{result['skipped']}</b>\n\n"
            "Теперь менеджеры могут использовать /today."
        )

    except Exception as e:
        traceback.print_exc()

        await message.answer(
            "❌ Не удалось загрузить портфель:\n"
            f"<code>{e}</code>"
        )


# =========================================================
# CONTACT FLOW
# =========================================================

@dp.callback_query(F.data.startswith("contact:"))
async def contact_clicked(
    callback: CallbackQuery,
):
    task_row = int(
        callback.data.split(":", 1)[1]
    )

    task = get_task_by_row(task_row)

    if not task:
        await callback.answer(
            "Задание не найдено",
            show_alert=True,
        )
        return

    manager = get_manager_by_telegram_id(
        callback.from_user.id
    )

    if not manager:
        await callback.answer(
            "Сначала /register",
            show_alert=True,
        )
        return

    client = normalize_text(
        task.get("client")
    )

    await callback.message.answer(
        f"Какой результат по клиенту <b>{client}</b>?",
        reply_markup=result_keyboard(task_row),
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("result:"))
async def result_clicked(
    callback: CallbackQuery,
    state: FSMContext,
):
    _, result_code, task_row_text = (
        callback.data.split(":", 2)
    )

    task_row = int(task_row_text)
    task = get_task_by_row(task_row)

    if not task:
        await callback.answer(
            "Задание не найдено",
            show_alert=True,
        )
        return

    result_title = dict(RESULT_OPTIONS).get(
        result_code,
        "Другое",
    )

    manager = get_manager_by_telegram_id(
        callback.from_user.id
    )

    client = normalize_text(
        task.get("client")
    )

    await state.update_data(
        flow="contact",
        task_row=task_row,
        client=client,
        manager=manager,
        result=result_title,
    )

    await state.set_state(
        ContactFlow.waiting_comment
    )

    await callback.message.answer(
        f"Клиент: <b>{client}</b>\n"
        f"Результат: <b>{result_title}</b>\n\n"
        "Напиши короткий комментарий.\n"
        "Если комментарий не нужен — отправь <code>-</code>."
    )

    await callback.answer()


@dp.message(ContactFlow.waiting_comment)
async def contact_comment(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    comment = (message.text or "").strip()

    if comment == "-":
        comment = ""

    await state.update_data(
        comment=comment
    )

    await message.answer(
        "Когда вернуться к клиенту?",
        reply_markup=next_contact_keyboard(
            int(data["task_row"])
        ),
    )


@dp.callback_query(F.data.startswith("next:"))
async def next_contact_clicked(
    callback: CallbackQuery,
    state: FSMContext,
):
    _, days_text, task_row_text = (
        callback.data.split(":", 2)
    )

    days = int(days_text)
    task_row = int(task_row_text)

    next_date = (
        today_local()
        + timedelta(days=days)
    )

    data = await state.get_data()

    save_communication(
        client=data["client"],
        manager=data["manager"],
        telegram_id=callback.from_user.id,
        result=data["result"],
        comment=data.get("comment", ""),
        next_contact=next_date,
        task_row=task_row,
    )

    await state.clear()

    await callback.message.answer(
        "✅ Контакт зафиксирован.\n"
        "Следующий контакт: "
        f"<b>{next_date.strftime('%d.%m.%Y')}</b>"
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("next_custom:"))
async def next_custom_clicked(
    callback: CallbackQuery,
    state: FSMContext,
):
    task_row = int(
        callback.data.split(":", 1)[1]
    )

    await state.update_data(
        task_row=task_row
    )

    await state.set_state(
        ContactFlow.waiting_custom_date
    )

    await callback.message.answer(
        "Напиши дату следующего контакта в формате:\n"
        "<code>25.08.2026</code>"
    )

    await callback.answer()


@dp.message(ContactFlow.waiting_custom_date)
async def custom_date_message(
    message: Message,
    state: FSMContext,
):
    try:
        next_date = datetime.strptime(
            (message.text or "").strip(),
            "%d.%m.%Y",
        ).date()

    except ValueError:
        await message.answer(
            "Не поняла дату. Используй формат ДД.ММ.ГГГГ."
        )
        return

    data = await state.get_data()

    save_communication(
        client=data["client"],
        manager=data["manager"],
        telegram_id=message.from_user.id,
        result=data["result"],
        comment=data.get("comment", ""),
        next_contact=next_date,
        task_row=int(data["task_row"]),
    )

    await state.clear()

    await message.answer(
        "✅ Контакт зафиксирован.\n"
        "Следующий контакт: "
        f"<b>{next_date.strftime('%d.%m.%Y')}</b>"
    )


# =========================================================
# POSTPONE FLOW
# =========================================================

@dp.callback_query(F.data.startswith("postpone:"))
async def postpone_clicked(
    callback: CallbackQuery,
    state: FSMContext,
):
    task_row = int(
        callback.data.split(":", 1)[1]
    )

    task = get_task_by_row(task_row)

    if not task:
        await callback.answer(
            "Задание не найдено",
            show_alert=True,
        )
        return

    manager = get_manager_by_telegram_id(
        callback.from_user.id
    )

    client = normalize_text(
        task.get("client")
    )

    await state.update_data(
        flow="postpone",
        task_row=task_row,
        client=client,
        manager=manager,
    )

    await state.set_state(
        ContactFlow.waiting_postpone_reason
    )

    await callback.message.answer(
        f"Почему откладываем <b>{client}</b>?\n\n"
        "Напиши короткую причину."
    )

    await callback.answer()


@dp.message(ContactFlow.waiting_postpone_reason)
async def postpone_reason(
    message: Message,
    state: FSMContext,
):
    reason = (message.text or "").strip()

    await state.update_data(
        reason=reason
    )

    data = await state.get_data()

    await message.answer(
        "На какой срок отложить?",
        reply_markup=postpone_keyboard(
            int(data["task_row"])
        ),
    )


@dp.callback_query(F.data.startswith("postpone_days:"))
async def postpone_days_clicked(
    callback: CallbackQuery,
    state: FSMContext,
):
    _, days_text, task_row_text = (
        callback.data.split(":", 2)
    )

    days = int(days_text)
    task_row = int(task_row_text)

    next_date = (
        today_local()
        + timedelta(days=days)
    )

    data = await state.get_data()

    save_postpone(
        client=data["client"],
        manager=data["manager"],
        telegram_id=callback.from_user.id,
        reason=data.get("reason", ""),
        next_contact=next_date,
        task_row=task_row,
    )

    await state.clear()

    await callback.message.answer(
        "⏰ Клиент отложен до "
        f"<b>{next_date.strftime('%d.%m.%Y')}</b>"
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("postpone_custom:"))
async def postpone_custom_clicked(
    callback: CallbackQuery,
    state: FSMContext,
):
    task_row = int(
        callback.data.split(":", 1)[1]
    )

    await state.update_data(
        task_row=task_row
    )

    await state.set_state(
        ContactFlow.waiting_postpone_date
    )

    await callback.message.answer(
        "Напиши дату в формате:\n"
        "<code>25.08.2026</code>"
    )

    await callback.answer()


@dp.message(ContactFlow.waiting_postpone_date)
async def postpone_custom_date(
    message: Message,
    state: FSMContext,
):
    try:
        next_date = datetime.strptime(
            (message.text or "").strip(),
            "%d.%m.%Y",
        ).date()

    except ValueError:
        await message.answer(
            "Не поняла дату. Используй формат ДД.ММ.ГГГГ."
        )
        return

    data = await state.get_data()

    save_postpone(
        client=data["client"],
        manager=data["manager"],
        telegram_id=message.from_user.id,
        reason=data.get("reason", ""),
        next_contact=next_date,
        task_row=int(data["task_row"]),
    )

    await state.clear()

    await message.answer(
        "⏰ Клиент отложен до "
        f"<b>{next_date.strftime('%d.%m.%Y')}</b>"
    )


# =========================================================
# REPORT
# =========================================================

async def send_manager_report(chat_id: int):
    ws = tasks_ws()
    headers = get_headers(ws)

    today_str = today_local().strftime("%Y-%m-%d")

    today_tasks = []

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(headers, values)

        if str(row.get("task_date", "")).strip() == today_str:
            today_tasks.append(row)

    if not today_tasks:
        await bot.send_message(
            chat_id,
            "📊 Сегодня задания менеджерам еще не создавались.",
        )
        return

    grouped = {}

    for task in today_tasks:
        manager = normalize_text(
            task.get("manager")
        )

        grouped.setdefault(
            manager,
            {
                "total": 0,
                "done": 0,
                "pending": [],
            },
        )

        grouped[manager]["total"] += 1

        status = str(
            task.get("status", "")
        ).strip()

        if status in {"done", "postponed"}:
            grouped[manager]["done"] += 1
        else:
            grouped[manager]["pending"].append(
                normalize_text(
                    task.get("client")
                )
            )

    lines = [
        (
            "📊 <b>Контроль коммуникации · "
            f"{today_local().strftime('%d.%m')}</b>"
        ),
        "",
    ]

    for manager, info in grouped.items():
        icon = (
            "✅"
            if info["done"] == info["total"]
            else "⚠️"
        )

        lines.append(
            f"{icon} <b>{manager}</b> — "
            f"{info['done']} / {info['total']}"
        )

    pending_all = []

    for manager, info in grouped.items():
        for client in info["pending"]:
            pending_all.append(
                f"• {client} — {manager}"
            )

    if pending_all:
        lines.extend(
            [
                "",
                "<b>Не обработаны:</b>",
            ]
        )

        lines.extend(pending_all)

    await bot.send_message(
        chat_id,
        "\n".join(lines)[:4000],
    )


# =========================================================
# DAILY LOOP
# =========================================================

async def daily_loop():
    last_sent_date = None
    last_report_date = None

    while True:
        try:
            now = now_local()
            hhmm = now.strftime("%H:%M")

            if (
                hhmm == DAILY_SEND_TIME
                and last_sent_date != now.date()
            ):
                managers = get_active_managers()

                for item in managers:
                    try:
                        await send_today_tasks(
                            item["telegram_id"],
                            item["manager"],
                        )
                    except Exception:
                        traceback.print_exc()

                last_sent_date = now.date()

            if (
                REPORT_CHAT_ID
                and hhmm == "18:00"
                and last_report_date != now.date()
            ):
                try:
                    await send_manager_report(
                        int(REPORT_CHAT_ID)
                    )
                except Exception:
                    traceback.print_exc()

                last_report_date = now.date()

        except Exception:
            traceback.print_exc()

        await asyncio.sleep(30)


# =========================================================
# RENDER WEB SERVER
# =========================================================

async def health(request):
    return web.Response(text="OK")


async def start_web_app():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )
    await site.start()

    print(
        f"WEB APP STARTED ON PORT {PORT}",
        flush=True,
    )


# =========================================================
# START
# =========================================================

async def main():
    print(
        "CLIENT COMMUNICATION BOT STARTING",
        flush=True,
    )

    print(
        (
            f"TIMEZONE={TIMEZONE}; "
            f"DAILY_SEND_TIME={DAILY_SEND_TIME}; "
            f"CLIENTS_PER_DAY={CLIENTS_PER_DAY}"
        ),
        flush=True,
    )

    await start_web_app()

    asyncio.create_task(
        daily_loop()
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
