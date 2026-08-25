import os
import json
import asyncio
import traceback
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from aiogram import F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


# =========================================================
# ATTENTION BOT SETTINGS
# =========================================================

GOOGLE_CREDENTIALS_JSON = os.getenv(
    "GOOGLE_CREDENTIALS_JSON",
    "",
).strip()

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "",
).strip()

TIMEZONE = os.getenv(
    "TIMEZONE",
    "Asia/Vladivostok",
).strip()

DAILY_SEND_TIME = os.getenv(
    "DAILY_SEND_TIME",
    "10:30",
).strip()

REPORT_CHAT_ID = os.getenv(
    "REPORT_CHAT_ID",
    "",
).strip()

CLIENTS_PER_DAY = int(
    os.getenv("CLIENTS_PER_DAY", "3")
)

TZ = ZoneInfo(TIMEZONE)

CONTACT_INTERVALS = {
    "Регулярный": 14,
    "Стабильный": 21,
    "Нерегулярный": 30,
}

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


class ContactFlow(StatesGroup):
    waiting_comment = State()
    waiting_custom_date = State()
    waiting_postpone_reason = State()
    waiting_postpone_date = State()


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


# =========================================================
# GOOGLE SHEETS
# =========================================================

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

    creds = Credentials.from_service_account_info(
        info,
        scopes=scopes,
    )

    gc = gspread.authorize(creds)
    return gc.open_by_key(GOOGLE_SHEET_ID)


def get_or_create_ws(title, headers):
    ss = get_spreadsheet()

    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(
            title=title,
            rows=3000,
            cols=max(12, len(headers)),
        )

    values = ws.get_all_values()

    if not values:
        ws.append_row(
            headers,
            value_input_option="USER_ENTERED",
        )
        return ws

    existing_headers = values[0]

    missing = [
        h
        for h in headers
        if h not in existing_headers
    ]

    if missing:
        merged = existing_headers + missing
        end = gspread.utils.rowcol_to_a1(
            1,
            len(merged),
        )
        ws.update(
            f"A1:{end}",
            [merged],
        )

    return ws


def clients_ws():
    return get_or_create_ws(
        "CLIENTS",
        CLIENT_HEADERS,
    )


def managers_ws():
    return get_or_create_ws(
        "MANAGERS",
        MANAGER_HEADERS,
    )


def tasks_ws():
    return get_or_create_ws(
        "DAILY_TASKS",
        TASK_HEADERS,
    )


def communications_ws():
    return get_or_create_ws(
        "COMMUNICATIONS",
        COMM_HEADERS,
    )


def get_headers(ws):
    values = ws.get_all_values()
    return values[0] if values else []


def row_to_dict(headers, values):
    values = values + [""] * max(
        0,
        len(headers) - len(values),
    )

    return dict(zip(headers, values))


def update_row_by_fields(ws, row_num, updates):
    headers = get_headers(ws)

    for field in updates:
        if field not in headers:
            headers.append(field)

    row = ws.row_values(row_num)
    row += [""] * max(
        0,
        len(headers) - len(row),
    )

    for field, value in updates.items():
        row[headers.index(field)] = (
            ""
            if value is None
            else str(value)
        )

    end = gspread.utils.rowcol_to_a1(
        row_num,
        len(headers),
    )

    ws.update(
        f"A{row_num}:{end}",
        [row],
    )


# =========================================================
# HELPERS
# =========================================================

def now_local():
    return datetime.now(TZ)


def today_local():
    return now_local().date()


def normalize_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value)
        .replace("\xa0", " ")
        .split()
    ).strip()


def parse_date(value):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    parsed = pd.to_datetime(
        value,
        errors="coerce",
        dayfirst=True,
    )

    if pd.isna(parsed):
        return None

    return parsed.date()


def fmt_date(value):
    d = parse_date(value)

    if d is None:
        return ""

    return d.strftime("%d.%m.%Y")


def pretty_date(value):
    d = parse_date(value)

    if d is None:
        return "—"

    return d.strftime("%d.%m.%Y")


def normalize_category(value):
    text = normalize_text(value).lower()

    if "нерегуляр" in text:
        return "Нерегулярный"

    if "регуляр" in text:
        return "Регулярный"

    if "стабил" in text:
        return "Стабильный"

    return normalize_text(value) or "Нерегулярный"


def normalize_group(value):
    return normalize_text(value).upper()


def find_column(df, aliases):
    normalized = {
        normalize_text(c).lower(): c
        for c in df.columns
    }

    for alias in aliases:
        key = normalize_text(alias).lower()

        if key in normalized:
            return normalized[key]

    return None


def client_key(client, manager):
    return (
        normalize_text(manager).lower(),
        normalize_text(client).lower(),
    )


# =========================================================
# PORTFOLIO SYNC
# =========================================================

def sync_portfolio_from_excel(path):
    df = pd.read_excel(path)

    client_col = find_column(
        df,
        ["Наименование", "Клиент", "Компания"],
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
        ["Признак", "Категория"],
    )

    group_col = find_column(
        df,
        ["Группа"],
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
            "Не найдены колонки: "
            + ", ".join(missing)
        )

    ws = clients_ws()
    headers = get_headers(ws)

    existing = {}

    for row_num, values in enumerate(
        ws.get_all_values()[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            values,
        )

        client = normalize_text(
            row.get("client")
        )

        manager = normalize_text(
            row.get("manager")
        )

        if client and manager:
            existing[
                client_key(client, manager)
            ] = (row_num, row)

    new_count = 0
    updated_count = 0

    for _, source_row in df.iterrows():
        client = normalize_text(
            source_row.get(client_col)
        )

        manager = normalize_text(
            source_row.get(manager_col)
        )

        if not client or not manager:
            continue

        data = {
            "client": client,
            "manager": manager,
            "category": normalize_category(
                source_row.get(category_col)
            ),
            "group": normalize_group(
                source_row.get(group_col)
            ),
            "last_order": fmt_date(
                source_row.get(last_order_col)
            ),
            "last_request": fmt_date(
                source_row.get(last_request_col)
            ),
            "active": "1",
        }

        key = client_key(
            client,
            manager,
        )

        if key in existing:
            row_num, old = existing[key]

            update_row_by_fields(
                ws,
                row_num,
                data,
            )

            updated_count += 1

        else:
            headers = get_headers(ws)

            new_row = {
                **data,
                "last_contact": "",
                "next_contact": "",
                "last_result": "",
            }

            ws.append_row(
                [
                    new_row.get(h, "")
                    for h in headers
                ],
                value_input_option="USER_ENTERED",
            )

            new_count += 1

    return {
        "new": new_count,
        "updated": updated_count,
    }


# =========================================================
# MANAGER REGISTRATION
# =========================================================

def register_manager(manager, telegram_id):
    ws = managers_ws()
    headers = get_headers(ws)

    for row_num, values in enumerate(
        ws.get_all_values()[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            values,
        )

        if (
            normalize_text(row.get("manager")).lower()
            == normalize_text(manager).lower()
        ):
            update_row_by_fields(
                ws,
                row_num,
                {
                    "manager": manager,
                    "telegram_id": telegram_id,
                    "active": "1",
                },
            )
            return

    ws.append_row(
        [
            manager,
            str(telegram_id),
            "1",
        ],
        value_input_option="USER_ENTERED",
    )


def get_manager_by_telegram_id(telegram_id):
    ws = managers_ws()
    headers = get_headers(ws)

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(
            headers,
            values,
        )

        if (
            str(row.get("telegram_id", "")).strip()
            == str(telegram_id)
            and str(row.get("active", "1")).strip() != "0"
        ):
            return normalize_text(
                row.get("manager")
            )

    return None


def get_active_managers():
    ws = managers_ws()
    headers = get_headers(ws)

    result = []

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(
            headers,
            values,
        )

        manager = normalize_text(
            row.get("manager")
        )

        telegram_id = str(
            row.get("telegram_id", "")
        ).strip()

        active = str(
            row.get("active", "1")
        ).strip()

        if manager and telegram_id and active != "0":
            result.append(
                {
                    "manager": manager,
                    "telegram_id": int(telegram_id),
                }
            )

    return result


# =========================================================
# CLIENT / TASK HELPERS
# =========================================================

def get_client_state(client, manager):
    ws = clients_ws()
    headers = get_headers(ws)

    for row_num, values in enumerate(
        ws.get_all_values()[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            values,
        )

        if client_key(
            row.get("client", ""),
            row.get("manager", ""),
        ) == client_key(
            client,
            manager,
        ):
            row["_row_num"] = row_num
            return row

    return {}


def update_client_state(
    client,
    manager,
    last_contact=None,
    next_contact=None,
    last_result=None,
):
    state = get_client_state(
        client,
        manager,
    )

    if not state:
        return

    updates = {}

    if last_contact is not None:
        updates["last_contact"] = last_contact

    if next_contact is not None:
        updates["next_contact"] = next_contact

    if last_result is not None:
        updates["last_result"] = last_result

    update_row_by_fields(
        clients_ws(),
        state["_row_num"],
        updates,
    )


def get_today_tasks(manager):
    ws = tasks_ws()
    headers = get_headers(ws)

    today_str = today_local().strftime(
        "%Y-%m-%d"
    )

    result = []

    for row_num, values in enumerate(
        ws.get_all_values()[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            values,
        )

        if (
            str(row.get("task_date", "")).strip()
            == today_str
            and normalize_text(row.get("manager")).lower()
            == normalize_text(manager).lower()
        ):
            row["_row_num"] = row_num
            result.append(row)

    return result


def get_or_create_daily_tasks(manager, telegram_id):
    existing = get_today_tasks(manager)

    if existing:
        return existing

    today = today_local()

    ws = clients_ws()
    headers = get_headers(ws)

    candidates = []

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(
            headers,
            values,
        )

        if str(row.get("active", "1")).strip() == "0":
            continue

        if (
            normalize_text(row.get("manager")).lower()
            != normalize_text(manager).lower()
        ):
            continue

        client = normalize_text(
            row.get("client")
        )

        if not client:
            continue

        category = normalize_category(
            row.get("category")
        )

        group_value = normalize_group(
            row.get("group")
        )

        interval = CONTACT_INTERVALS.get(
            category,
            30,
        )

        last_contact = parse_date(
            row.get("last_contact")
        )

        next_contact = parse_date(
            row.get("next_contact")
        )

        last_order = parse_date(
            row.get("last_order")
        )

        last_request = parse_date(
            row.get("last_request")
        )

        if next_contact and next_contact > today:
            continue

        if next_contact:
            due_date = next_contact

        elif last_contact:
            due_date = (
                last_contact
                + timedelta(days=interval)
            )

        else:
            anchors = [
                x
                for x in (
                    last_order,
                    last_request,
                )
                if x
            ]

            if anchors:
                due_date = (
                    max(anchors)
                    + timedelta(days=interval)
                )
            else:
                due_date = date(2000, 1, 1)

        if due_date > today:
            continue

        overdue_days = (
            today - due_date
        ).days

        candidates.append(
            {
                "client": client,
                "overdue": overdue_days,
                "group_rank": GROUP_PRIORITY.get(
                    group_value,
                    0,
                ),
                "category_rank": {
                    "Регулярный": 3,
                    "Стабильный": 2,
                    "Нерегулярный": 1,
                }.get(category, 0),
            }
        )

    candidates.sort(
        key=lambda x: (
            -x["overdue"],
            -x["group_rank"],
            -x["category_rank"],
            x["client"].lower(),
        )
    )

    selected = candidates[
        :CLIENTS_PER_DAY
    ]

    tws = tasks_ws()
    created_at = now_local().strftime(
        "%d.%m.%Y %H:%M"
    )

    today_str = today.strftime(
        "%Y-%m-%d"
    )

    for item in selected:
        tws.append_row(
            [
                today_str,
                manager,
                str(telegram_id),
                item["client"],
                "new",
                created_at,
                "",
            ],
            value_input_option="USER_ENTERED",
        )

    return get_today_tasks(manager)


def get_task_by_row(task_row):
    ws = tasks_ws()
    headers = get_headers(ws)
    values = ws.row_values(task_row)

    if not values:
        return None

    row = row_to_dict(
        headers,
        values,
    )

    row["_row_num"] = task_row
    return row


def mark_task_status(task_row, status):
    update_row_by_fields(
        tasks_ws(),
        task_row,
        {
            "status": status,
            "completed_at": (
                now_local().strftime(
                    "%d.%m.%Y %H:%M"
                )
                if status in {
                    "done",
                    "postponed",
                }
                else ""
            ),
        },
    )


# =========================================================
# SAVE RESULT
# =========================================================

def save_communication(
    client,
    manager,
    telegram_id,
    result,
    comment,
    next_contact,
    task_row,
):
    next_text = (
        next_contact.strftime("%d.%m.%Y")
        if next_contact
        else ""
    )

    communications_ws().append_row(
        [
            today_local().strftime("%d.%m.%Y"),
            manager,
            str(telegram_id),
            client,
            result,
            comment,
            next_text,
            "telegram",
            now_local().strftime("%d.%m.%Y %H:%M"),
        ],
        value_input_option="USER_ENTERED",
    )

    update_client_state(
        client=client,
        manager=manager,
        last_contact=today_local().strftime("%d.%m.%Y"),
        next_contact=next_text,
        last_result=result,
    )

    mark_task_status(
        task_row,
        "done",
    )


def save_postpone(
    client,
    manager,
    telegram_id,
    reason,
    next_contact,
    task_row,
):
    next_text = next_contact.strftime(
        "%d.%m.%Y"
    )

    communications_ws().append_row(
        [
            today_local().strftime("%d.%m.%Y"),
            manager,
            str(telegram_id),
            client,
            "Отложено",
            reason,
            next_text,
            "telegram",
            now_local().strftime("%d.%m.%Y %H:%M"),
        ],
        value_input_option="USER_ENTERED",
    )

    update_client_state(
        client=client,
        manager=manager,
        next_contact=next_text,
        last_result=f"Отложено: {reason}",
    )

    mark_task_status(
        task_row,
        "postponed",
    )


# =========================================================
# KEYBOARDS
# =========================================================

def client_actions_keyboard(task_row):
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


def result_keyboard(task_row):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=(
                        f"result:{code}:{task_row}"
                    ),
                )
            ]
            for code, title in RESULT_OPTIONS
        ]
    )


def next_contact_keyboard(task_row):
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


# =========================================================
# SEND
# =========================================================

async def send_today_tasks(
    bot,
    telegram_id,
    manager,
):
    tasks = get_or_create_daily_tasks(
        manager,
        telegram_id,
    )

    active = [
        task
        for task in tasks
        if str(task.get("status", "")).strip()
        not in {
            "done",
            "postponed",
        }
    ]

    if not active:
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
            f"Количество: <b>{len(active)}</b>"
        ),
    )

    for index, task in enumerate(
        active,
        start=1,
    ):
        client = normalize_text(
            task.get("client")
        )

        state = get_client_state(
            client,
            manager,
        )

        await bot.send_message(
            telegram_id,
            (
                f"<b>{index}. {client}</b>\n"
                f"Группа: <b>{normalize_group(state.get('group')) or '—'}</b>\n"
                f"Признак: <b>{normalize_category(state.get('category'))}</b>\n"
                f"Последний контакт: <b>{pretty_date(state.get('last_contact'))}</b>\n"
                f"Последний заказ: {pretty_date(state.get('last_order'))}\n"
                f"Последний запрос: {pretty_date(state.get('last_request'))}"
            ),
            reply_markup=client_actions_keyboard(
                int(task["_row_num"])
            ),
        )


# =========================================================
# HANDLERS
# =========================================================

def register_attention_handlers(dp, bot):
    @dp.message(Command("register"))
    async def register(message: Message):
        manager = (
            message.text
            .replace("/register", "", 1)
            .strip()
        )

        if not manager:
            await message.answer(
                "Укажи ФИО менеджера.\n"
                "Например:\n"
                "<code>/register Лилия Буглак</code>"
            )
            return

        register_manager(
            manager,
            message.from_user.id,
        )

        await message.answer(
            "✅ Telegram привязан:\n"
            f"<b>{manager}</b>"
        )

    @dp.message(Command("status"))
    async def status(message: Message):
        manager = get_manager_by_telegram_id(
            message.from_user.id
        )

        if manager:
            await message.answer(
                f"✅ Вы зарегистрированы как:\n"
                f"<b>{manager}</b>"
            )
        else:
            await message.answer(
                "⚠️ Сначала выполните:\n"
                "<code>/register ФИО</code>"
            )

    @dp.message(Command("today"))
    async def today(message: Message):
        manager = get_manager_by_telegram_id(
            message.from_user.id
        )

        if not manager:
            await message.answer(
                "Сначала выполните:\n"
                "<code>/register ФИО</code>"
            )
            return

        await send_today_tasks(
            bot,
            message.from_user.id,
            manager,
        )

    @dp.callback_query(F.data.startswith("contact:"))
    async def contact(callback: CallbackQuery):
        task_row = int(
            callback.data.split(":")[1]
        )

        task = get_task_by_row(task_row)

        if not task:
            await callback.answer(
                "Задание не найдено",
                show_alert=True,
            )
            return

        await callback.message.answer(
            (
                "Какой результат по клиенту "
                f"<b>{task.get('client')}</b>?"
            ),
            reply_markup=result_keyboard(
                task_row
            ),
        )

        await callback.answer()

    @dp.callback_query(F.data.startswith("result:"))
    async def result(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        _, result_code, task_row_text = (
            callback.data.split(":", 2)
        )

        task_row = int(
            task_row_text
        )

        task = get_task_by_row(
            task_row
        )

        manager = get_manager_by_telegram_id(
            callback.from_user.id
        )

        result_title = dict(
            RESULT_OPTIONS
        ).get(
            result_code,
            "Другое",
        )

        await state.update_data(
            task_row=task_row,
            client=task.get("client"),
            manager=manager,
            result=result_title,
        )

        await state.set_state(
            ContactFlow.waiting_comment
        )

        await callback.message.answer(
            "Напиши короткий комментарий.\n"
            "Если не нужен — отправь <code>-</code>."
        )

        await callback.answer()

    @dp.message(ContactFlow.waiting_comment)
    async def comment(
        message: Message,
        state: FSMContext,
    ):
        data = await state.get_data()

        comment_text = (
            message.text or ""
        ).strip()

        if comment_text == "-":
            comment_text = ""

        await state.update_data(
            comment=comment_text
        )

        await message.answer(
            "Когда вернуться к клиенту?",
            reply_markup=next_contact_keyboard(
                int(data["task_row"])
            ),
        )

    @dp.callback_query(F.data.startswith("next:"))
    async def next_contact(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        _, days_text, task_row_text = (
            callback.data.split(":", 2)
        )

        data = await state.get_data()

        next_date = (
            today_local()
            + timedelta(
                days=int(days_text)
            )
        )

        save_communication(
            client=data["client"],
            manager=data["manager"],
            telegram_id=callback.from_user.id,
            result=data["result"],
            comment=data.get("comment", ""),
            next_contact=next_date,
            task_row=int(task_row_text),
        )

        await state.clear()

        await callback.message.answer(
            "✅ Контакт зафиксирован.\n"
            f"Следующий: <b>{next_date.strftime('%d.%m.%Y')}</b>"
        )

        await callback.answer()

    @dp.callback_query(F.data.startswith("postpone:"))
    async def postpone(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        task_row = int(
            callback.data.split(":")[1]
        )

        task = get_task_by_row(
            task_row
        )

        manager = get_manager_by_telegram_id(
            callback.from_user.id
        )

        await state.update_data(
            task_row=task_row,
            client=task.get("client"),
            manager=manager,
        )

        await state.set_state(
            ContactFlow.waiting_postpone_reason
        )

        await callback.message.answer(
            "Почему откладываем? "
            "Напиши короткую причину."
        )

        await callback.answer()

    @dp.message(ContactFlow.waiting_postpone_reason)
    async def postpone_reason(
        message: Message,
        state: FSMContext,
    ):
        data = await state.get_data()

        reason = (
            message.text or ""
        ).strip()

        # Для первой версии после причины
        # откладываем на 14 дней.
        next_date = (
            today_local()
            + timedelta(days=14)
        )

        save_postpone(
            client=data["client"],
            manager=data["manager"],
            telegram_id=message.from_user.id,
            reason=reason,
            next_contact=next_date,
            task_row=int(data["task_row"]),
        )

        await state.clear()

        await message.answer(
            "⏰ Клиент отложен на 14 дней, до "
            f"<b>{next_date.strftime('%d.%m.%Y')}</b>"
        )


# =========================================================
# REPORT + SCHEDULER
# =========================================================

async def send_report(bot, chat_id):
    ws = tasks_ws()
    headers = get_headers(ws)

    today_str = today_local().strftime(
        "%Y-%m-%d"
    )

    grouped = {}

    for values in ws.get_all_values()[1:]:
        row = row_to_dict(
            headers,
            values,
        )

        if str(row.get("task_date", "")).strip() != today_str:
            continue

        manager = normalize_text(
            row.get("manager")
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

        if str(row.get("status", "")).strip() in {
            "done",
            "postponed",
        }:
            grouped[manager]["done"] += 1
        else:
            grouped[manager]["pending"].append(
                normalize_text(
                    row.get("client")
                )
            )

    if not grouped:
        return

    lines = [
        "📊 <b>Контроль коммуникации</b>",
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

    pending = []

    for manager, info in grouped.items():
        for client in info["pending"]:
            pending.append(
                f"• {client} — {manager}"
            )

    if pending:
        lines.extend(
            [
                "",
                "<b>Не обработаны:</b>",
                *pending,
            ]
        )

    await bot.send_message(
        chat_id,
        "\n".join(lines)[:4000],
    )


async def start_attention_scheduler(bot):
    last_send = None
    last_report = None

    while True:
        try:
            now = now_local()
            hhmm = now.strftime("%H:%M")

            if (
                hhmm == DAILY_SEND_TIME
                and last_send != now.date()
            ):
                for item in get_active_managers():
                    try:
                        await send_today_tasks(
                            bot,
                            item["telegram_id"],
                            item["manager"],
                        )
                    except Exception:
                        traceback.print_exc()

                last_send = now.date()

            if (
                REPORT_CHAT_ID
                and hhmm == "18:00"
                and last_report != now.date()
            ):
                await send_report(
                    bot,
                    int(REPORT_CHAT_ID),
                )

                last_report = now.date()

        except Exception:
            traceback.print_exc()

        await asyncio.sleep(30)

