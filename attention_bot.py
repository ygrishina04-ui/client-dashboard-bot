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
from aiogram.filters import Command, CommandStart
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

REPORT_SEND_TIME = os.getenv(
    "REPORT_SEND_TIME",
    "18:00",
).strip()

CLIENTS_PER_DAY = int(
    os.getenv("CLIENTS_PER_DAY", "3")
)

# Python weekday(): ПН=0, ВТ=1, СР=2, ЧТ=3, ПТ=4, СБ=5, ВС=6
AUTO_SEND_WEEKDAYS = {
    1,  # вторник
    2,  # среда
    3,  # четверг
}

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


# =========================================================
# FSM
# =========================================================

class ContactFlow(StatesGroup):
    waiting_comment = State()
    waiting_custom_date = State()
    waiting_postpone_reason = State()
    waiting_postpone_date = State()


# =========================================================
# GOOGLE SHEETS STRUCTURE
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
    "card_message_id",
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
# GOOGLE SHEETS BASE
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

    return gc.open_by_key(
        GOOGLE_SHEET_ID
    )


def get_or_create_ws(title, headers):
    """
    Один вызов для получения/создания листа.
    Важно: не используем get_all_values() повторно внутри циклов.
    """
    ss = get_spreadsheet()

    try:
        ws = ss.worksheet(title)

    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(
            title=title,
            rows=3000,
            cols=max(12, len(headers)),
        )

        ws.append_row(
            headers,
            value_input_option="USER_ENTERED",
        )

        return ws

    # Одно чтение первой строки.
    existing_headers = ws.row_values(1)

    if not existing_headers:
        ws.append_row(
            headers,
            value_input_option="USER_ENTERED",
        )

    else:
        missing = [
            h for h in headers
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


def row_to_dict(headers, values):
    padded = values + [""] * max(
        0,
        len(headers) - len(values),
    )

    return dict(
        zip(headers, padded)
    )


def get_headers(ws):
    values = ws.get_all_values()
    return values[0] if values else []


def normalize_sheet_headers(
    ws,
    required_headers,
    current_headers=None,
):
    """
    Добавляет отсутствующие колонки максимум одним update.
    """
    headers = (
        list(current_headers)
        if current_headers is not None
        else ws.row_values(1)
    )

    if not headers:
        headers = list(required_headers)

        end = gspread.utils.rowcol_to_a1(
            1,
            len(headers),
        )

        ws.update(
            f"A1:{end}",
            [headers],
        )

        return headers

    changed = False

    for field in required_headers:
        if field not in headers:
            headers.append(field)
            changed = True

    if changed:
        end = gspread.utils.rowcol_to_a1(
            1,
            len(headers),
        )

        ws.update(
            f"A1:{end}",
            [headers],
        )

    return headers


def update_row_by_fields(
    ws,
    row_num,
    updates,
):
    """
    Для единичных действий пользователя.
    Не используется при массовом импорте портфеля.
    """
    values = ws.get_all_values()

    if not values:
        return

    headers = normalize_sheet_headers(
        ws,
        list(updates.keys()),
        values[0],
    )

    # Если после добавления заголовков данных меньше — расширяем.
    row = (
        values[row_num - 1][:]
        if len(values) >= row_num
        else []
    )

    row += [""] * max(
        0,
        len(headers) - len(row),
    )

    for field, value in updates.items():
        idx = headers.index(field)

        row[idx] = (
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
    """
    Безопасно обрабатывает:
    None / NaN / NaT / Timestamp / datetime / date / str
    """
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

    return d.strftime(
        "%d.%m.%Y"
    )


def pretty_date(value):
    d = parse_date(value)

    if d is None:
        return "—"

    return d.strftime(
        "%d.%m.%Y"
    )


def normalize_category(value):
    text = normalize_text(
        value
    ).lower()

    # Сначала нерегулярный, потому что в нем есть слово "регуляр".
    if "нерегуляр" in text:
        return "Нерегулярный"

    if "регуляр" in text:
        return "Регулярный"

    if "стабил" in text:
        return "Стабильный"

    return (
        normalize_text(value)
        or "Нерегулярный"
    )


def normalize_group(value):
    return normalize_text(
        value
    ).upper()


def find_column(df, aliases):
    normalized = {
        normalize_text(c).lower(): c
        for c in df.columns
    }

    for alias in aliases:
        key = normalize_text(
            alias
        ).lower()

        if key in normalized:
            return normalized[key]

    return None


def client_key(client, manager):
    return (
        normalize_text(manager).lower(),
        normalize_text(client).lower(),
    )


def normalize_person_name(value):
    text = normalize_text(value).lower()

    for ch in '.,;:"()[]{}':
        text = text.replace(ch, " ")

    return [
        part
        for part in text.split()
        if part
    ]


def manager_names_match(left, right):
    left_text = normalize_text(left).lower()
    right_text = normalize_text(right).lower()

    if left_text == right_text:
        return True

    left_parts = normalize_person_name(left)
    right_parts = normalize_person_name(right)

    # Позволяет "Лилия Буглак" == "Буглак Лилия",
    # но не склеивает разных людей по одной фамилии.
    return (
        len(left_parts) >= 2
        and len(right_parts) >= 2
        and set(left_parts) == set(right_parts)
    )


def resolve_manager_name_from_clients(manager):
    """
    Возвращает точное написание менеджера из листа CLIENTS.
    """
    ws = clients_ws()
    values = ws.get_all_values()

    if not values:
        return None

    headers = values[0]

    for row_values in values[1:]:
        row = row_to_dict(headers, row_values)
        saved_manager = normalize_text(row.get("manager"))

        if saved_manager and manager_names_match(saved_manager, manager):
            return saved_manager

    return None


def count_clients_for_manager(manager):
    ws = clients_ws()
    values = ws.get_all_values()

    if not values:
        return 0

    headers = values[0]
    count = 0

    for row_values in values[1:]:
        row = row_to_dict(headers, row_values)

        if (
            str(row.get("active", "1")).strip() != "0"
            and manager_names_match(row.get("manager"), manager)
        ):
            count += 1

    return count


# =========================================================
# PORTFOLIO SYNC — OPTIMIZED
# =========================================================

def sync_portfolio_from_excel(path):
    """
    Массовая синхронизация без сотен чтений Google Sheets.

    Google Sheets:
    - CLIENTS читается один раз;
    - существующие строки обновляются через один batch_update;
    - новые клиенты добавляются одним append_rows.
    """

    df = pd.read_excel(path)

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
        missing.append(
            "Наименование"
        )

    if not manager_col:
        missing.append(
            "Оперативный менеджер"
        )

    if not category_col:
        missing.append(
            "Признак"
        )

    if not group_col:
        missing.append(
            "Группа"
        )

    if not last_order_col:
        missing.append(
            "Дата последнего заказа"
        )

    if not last_request_col:
        missing.append(
            "Дата последнего запроса"
        )

    if missing:
        raise ValueError(
            "Не найдены колонки: "
            + ", ".join(missing)
        )

    ws = clients_ws()

    # КЛЮЧЕВОЕ: ровно одно массовое чтение CLIENTS.
    all_values = ws.get_all_values()

    if not all_values:
        headers = list(
            CLIENT_HEADERS
        )

        ws.append_row(
            headers,
            value_input_option="USER_ENTERED",
        )

        all_values = [
            headers
        ]

    else:
        headers = normalize_sheet_headers(
            ws,
            CLIENT_HEADERS,
            all_values[0],
        )

        # Если заголовки расширились, данных в памяти еще нет в новых колонках.
        if len(headers) > len(all_values[0]):
            for i in range(
                1,
                len(all_values),
            ):
                all_values[i] += (
                    [""] * (
                        len(headers)
                        - len(all_values[i])
                    )
                )

    existing = {}

    for row_num, values in enumerate(
        all_values[1:],
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
                client_key(
                    client,
                    manager,
                )
            ] = {
                "row_num": row_num,
                "row": row,
            }

    batch_updates = []
    append_rows_data = []

    new_count = 0
    updated_count = 0
    skipped_count = 0

    # Чтобы клиенты, перемещенные между менеджерами,
    # не создавали странных пустых строк, пока ключ = клиент+менеджер.
    for _, source_row in df.iterrows():
        client = normalize_text(
            source_row.get(
                client_col
            )
        )

        manager = normalize_text(
            source_row.get(
                manager_col
            )
        )

        if not client or not manager:
            skipped_count += 1
            continue

        source_data = {
            "client": client,
            "manager": manager,
            "category": normalize_category(
                source_row.get(
                    category_col
                )
            ),
            "group": normalize_group(
                source_row.get(
                    group_col
                )
            ),
            "last_order": fmt_date(
                source_row.get(
                    last_order_col
                )
            ),
            "last_request": fmt_date(
                source_row.get(
                    last_request_col
                )
            ),
            "active": "1",
        }

        key = client_key(
            client,
            manager,
        )

        if key in existing:
            current = existing[
                key
            ]["row"].copy()

            # Не затираем:
            # last_contact / next_contact / last_result.
            current.update(
                source_data
            )

            row_values = [
                current.get(
                    header,
                    "",
                )
                for header in headers
            ]

            row_num = existing[
                key
            ]["row_num"]

            end = gspread.utils.rowcol_to_a1(
                row_num,
                len(headers),
            )

            batch_updates.append(
                {
                    "range": (
                        f"A{row_num}:{end}"
                    ),
                    "values": [
                        row_values
                    ],
                }
            )

            updated_count += 1

        else:
            new_row = {
                **source_data,
                "last_contact": "",
                "next_contact": "",
                "last_result": "",
            }

            append_rows_data.append(
                [
                    new_row.get(
                        header,
                        "",
                    )
                    for header in headers
                ]
            )

            new_count += 1

    # Одно пакетное обновление существующих клиентов.
    if batch_updates:
        # Делим на разумные чанки, чтобы не делать гигантский payload.
        chunk_size = 200

        for i in range(
            0,
            len(batch_updates),
            chunk_size,
        ):
            ws.batch_update(
                batch_updates[
                    i:i + chunk_size
                ],
                value_input_option="USER_ENTERED",
            )

    # Один append_rows для новых клиентов.
    if append_rows_data:
        ws.append_rows(
            append_rows_data,
            value_input_option="USER_ENTERED",
        )

    return {
        "new": new_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "source_rows": len(df),
    }


# =========================================================
# MANAGER REGISTRATION
# =========================================================

def register_manager(manager, telegram_id):
    ws = managers_ws()
    values = ws.get_all_values()

    if not values:
        ws.append_row(
            MANAGER_HEADERS,
            value_input_option="USER_ENTERED",
        )
        values = [MANAGER_HEADERS]

    headers = values[0]

    missing_headers = [
        h for h in MANAGER_HEADERS
        if h not in headers
    ]

    if missing_headers:
        headers = headers + missing_headers
        end_cell = gspread.utils.rowcol_to_a1(1, len(headers))
        ws.update(
            f"A1:{end_cell}",
            [headers],
        )

    manager_norm = normalize_text(manager).lower()
    target_id = str(telegram_id).strip()

    telegram_row_num = None
    manager_row_num = None

    for row_num, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            row_values,
        )

        saved_manager = normalize_text(
            row.get("manager", "")
        ).lower()

        saved_id = str(
            row.get("telegram_id", "")
        ).strip().lstrip("'")

        if saved_id.endswith(".0"):
            saved_id = saved_id[:-2]

        if saved_id == target_id:
            telegram_row_num = row_num

        if saved_manager == manager_norm:
            manager_row_num = row_num

    if telegram_row_num:
        update_row_by_fields(
            ws,
            telegram_row_num,
            {
                "manager": manager,
                "telegram_id": f"'{telegram_id}",
                "active": "1",
            },
        )
        return

    if manager_row_num:
        update_row_by_fields(
            ws,
            manager_row_num,
            {
                "manager": manager,
                "telegram_id": f"'{telegram_id}",
                "active": "1",
            },
        )
        return

    data = {
        "manager": manager,
        "telegram_id": f"'{telegram_id}",
        "active": "1",
    }

    ws.append_row(
        [
            data.get(header, "")
            for header in headers
        ],
        value_input_option="USER_ENTERED",
    )

def get_manager_by_telegram_id(telegram_id):
    ws = managers_ws()
    values = ws.get_all_values()

    if not values:
        return None

    headers = values[0]
    target_id = str(telegram_id).strip()

    for row_values in values[1:]:
        row = row_to_dict(
            headers,
            row_values,
        )

        saved_id = str(
            row.get("telegram_id", "")
        ).strip().lstrip("'")

        if saved_id.endswith(".0"):
            saved_id = saved_id[:-2]

        active = str(
            row.get("active", "1")
        ).strip().lower()

        if (
            saved_id == target_id
            and active not in {
                "0",
                "false",
                "нет",
                "no",
            }
        ):
            return normalize_text(
                row.get("manager")
            )

    return None

def get_active_managers():
    ws = managers_ws()

    values = ws.get_all_values()

    if not values:
        return []

    headers = values[0]

    result = []

    for row_values in values[1:]:
        row = row_to_dict(
            headers,
            row_values,
        )

        manager = normalize_text(
            row.get(
                "manager"
            )
        )

        telegram_id = str(
            row.get(
                "telegram_id",
                "",
            )
        ).strip()

        active = str(
            row.get(
                "active",
                "1",
            )
        ).strip()

        if (
            manager
            and telegram_id
            and active != "0"
        ):
            result.append(
                {
                    "manager": manager,
                    "telegram_id": int(
                        telegram_id
                    ),
                }
            )

    return result


# =========================================================
# CLIENT STATE
# =========================================================

def get_client_state(
    client,
    manager,
):
    ws = clients_ws()

    values = ws.get_all_values()

    if not values:
        return {}

    headers = values[0]

    target_key = client_key(
        client,
        manager,
    )

    for row_num, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            row_values,
        )

        if client_key(
            row.get(
                "client",
                "",
            ),
            row.get(
                "manager",
                "",
            ),
        ) == target_key:
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
        updates[
            "last_contact"
        ] = last_contact

    if next_contact is not None:
        updates[
            "next_contact"
        ] = next_contact

    if last_result is not None:
        updates[
            "last_result"
        ] = last_result

    if not updates:
        return

    update_row_by_fields(
        clients_ws(),
        state[
            "_row_num"
        ],
        updates,
    )


# =========================================================
# DAILY TASKS
# =========================================================

def get_today_tasks(
    manager,
):
    ws = tasks_ws()

    values = ws.get_all_values()

    if not values:
        return []

    headers = values[0]

    today_str = (
        today_local()
        .strftime(
            "%Y-%m-%d"
        )
    )

    result = []

    for row_num, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = row_to_dict(
            headers,
            row_values,
        )

        if (
            str(
                row.get(
                    "task_date",
                    "",
                )
            ).strip()
            == today_str
            and normalize_text(
                row.get(
                    "manager"
                )
            ).lower()
            == normalize_text(
                manager
            ).lower()
        ):
            row[
                "_row_num"
            ] = row_num

            result.append(
                row
            )

    return result


def count_eligible_clients_for_manager(manager):
    today = today_local()

    ws = clients_ws()
    values = ws.get_all_values()

    if not values:
        return 0

    headers = values[0]
    count = 0

    for row_values in values[1:]:
        row = row_to_dict(headers, row_values)

        if str(row.get("active", "1")).strip() == "0":
            continue

        row_manager = normalize_text(row.get("manager"))
        if row_manager.lower() != normalize_text(manager).lower():
            continue

        client = normalize_text(row.get("client"))
        if not client:
            continue

        category = normalize_category(row.get("category"))
        interval = CONTACT_INTERVALS.get(category, 30)

        last_contact = parse_date(row.get("last_contact"))
        next_contact = parse_date(row.get("next_contact"))
        last_order = parse_date(row.get("last_order"))
        last_request = parse_date(row.get("last_request"))

        recent_order = (
            last_order is not None
            and (today - last_order).days <= 25
        )
        recent_request = (
            last_request is not None
            and (today - last_request).days <= 25
        )

        if recent_order or recent_request:
            continue

        if next_contact and next_contact > today:
            continue

        if last_contact and not next_contact:
            due_date = last_contact + timedelta(days=interval)
            if due_date > today:
                continue

        count += 1

    return count


def get_or_create_daily_tasks(
    manager,
    telegram_id,
):
    existing = get_today_tasks(
        manager
    )

    if existing:
        return existing

    today = today_local()

    ws = clients_ws()

    values = ws.get_all_values()

    if not values:
        return []

    headers = values[0]

    candidates = []

    for row_values in values[1:]:
        row = row_to_dict(
            headers,
            row_values,
        )

        if str(
            row.get(
                "active",
                "1",
            )
        ).strip() == "0":
            continue

        if not manager_names_match(
            row.get("manager"),
            manager,
        ):
            continue

        client = normalize_text(
            row.get(
                "client"
            )
        )

        if not client:
            continue

        category = normalize_category(
            row.get(
                "category"
            )
        )

        group_value = normalize_group(
            row.get(
                "group"
            )
        )

        interval = CONTACT_INTERVALS.get(
            category,
            30,
        )

        last_contact = parse_date(
            row.get(
                "last_contact"
            )
        )

        next_contact = parse_date(
            row.get(
                "next_contact"
            )
        )

        last_order = parse_date(
            row.get(
                "last_order"
            )
        )

        last_request = parse_date(
            row.get(
                "last_request"
            )
        )

        # Если был заказ ИЛИ запрос за последние 25 дней —
        # клиент не попадает в ежедневную выборку.
        recent_order = (
            last_order is not None
            and (today - last_order).days <= 25
        )

        recent_request = (
            last_request is not None
            and (today - last_request).days <= 25
        )

        if recent_order or recent_request:
            continue

        # Отложен до будущего.
        if (
            next_contact
            and next_contact > today
        ):
            continue

        if next_contact:
            due_date = next_contact

        elif last_contact:
            due_date = (
                last_contact
                + timedelta(
                    days=interval
                )
            )

        else:
            # Первый цикл работы:
            # если коммуникация еще ни разу не зафиксирована,
            # клиент сразу участвует в ежедневной выборке.
            #
            # Последний заказ/запрос используем только для приоритета:
            # чем дольше не было коммерческой активности,
            # тем выше клиент поднимается в очереди.
            anchors = [
                d
                for d in (
                    last_order,
                    last_request,
                )
                if d
            ]

            if anchors:
                due_date = min(
                    max(anchors),
                    today
                )
            else:
                due_date = date(
                    2000,
                    1,
                    1,
                )

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
                }.get(
                    category,
                    0,
                ),
            }
        )

    candidates.sort(
        key=lambda x: (
            -x[
                "overdue"
            ],
            -x[
                "group_rank"
            ],
            -x[
                "category_rank"
            ],
            x[
                "client"
            ].lower(),
        )
    )

    selected = candidates[
        :CLIENTS_PER_DAY
    ]

    if not selected:
        return []

    tws = tasks_ws()

    today_str = today.strftime(
        "%Y-%m-%d"
    )

    created_at = now_local().strftime(
        "%d.%m.%Y %H:%M"
    )

    rows = [
        [
            today_str,
            manager,
            str(
                telegram_id
            ),
            item[
                "client"
            ],
            "new",
            created_at,
            "",
            "",
        ]
        for item in selected
    ]

    # Одним запросом.
    tws.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )

    return get_today_tasks(
        manager
    )


def get_task_by_row(
    task_row,
):
    ws = tasks_ws()

    values = ws.get_all_values()

    if (
        not values
        or task_row < 2
        or task_row > len(
            values
        )
    ):
        return None

    headers = values[0]

    row = row_to_dict(
        headers,
        values[
            task_row - 1
        ],
    )

    row[
        "_row_num"
    ] = task_row

    return row


def mark_task_status(
    task_row,
    status,
):
    update_row_by_fields(
        tasks_ws(),
        task_row,
        {
            "status": status,
            "completed_at": (
                now_local()
                .strftime(
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
        next_contact.strftime(
            "%d.%m.%Y"
        )
        if next_contact
        else ""
    )

    communications_ws().append_row(
        [
            today_local().strftime(
                "%d.%m.%Y"
            ),
            manager,
            str(
                telegram_id
            ),
            client,
            result,
            comment,
            next_text,
            "telegram",
            now_local().strftime(
                "%d.%m.%Y %H:%M"
            ),
        ],
        value_input_option="USER_ENTERED",
    )

    update_client_state(
        client=client,
        manager=manager,
        last_contact=today_local().strftime(
            "%d.%m.%Y"
        ),
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
    next_text = (
        next_contact
        .strftime(
            "%d.%m.%Y"
        )
    )

    communications_ws().append_row(
        [
            today_local().strftime(
                "%d.%m.%Y"
            ),
            manager,
            str(
                telegram_id
            ),
            client,
            "Отложено",
            reason,
            next_text,
            "telegram",
            now_local().strftime(
                "%d.%m.%Y %H:%M"
            ),
        ],
        value_input_option="USER_ENTERED",
    )

    update_client_state(
        client=client,
        manager=manager,
        next_contact=next_text,
        last_result=(
            f"Отложено: {reason}"
        ),
    )

    mark_task_status(
        task_row,
        "postponed",
    )



# =========================================================
# MESSAGE CLEANUP
# =========================================================

async def remember_chain_message(state, message_id):
    data = await state.get_data()

    ids = list(
        data.get(
            "chain_message_ids",
            [],
        )
    )

    if message_id not in ids:
        ids.append(
            message_id
        )

    await state.update_data(
        chain_message_ids=ids
    )


async def safe_delete_message(
    bot,
    chat_id,
    message_id,
):
    if not message_id:
        return

    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=int(message_id),
        )
    except Exception:
        # Сообщение могло быть уже удалено,
        # слишком старым или недоступным.
        pass


async def cleanup_client_chain(
    bot,
    chat_id,
    task_row,
    extra_message_ids=None,
):
    ids = set()

    task = get_task_by_row(
        task_row
    )

    if task:
        card_id = str(
            task.get(
                "card_message_id",
                "",
            )
        ).strip()

        if card_id:
            try:
                ids.add(
                    int(float(card_id))
                )
            except Exception:
                pass

    for message_id in (
        extra_message_ids or []
    ):
        try:
            ids.add(
                int(message_id)
            )
        except Exception:
            pass

    # Удаляем от новых сообщений к старым.
    for message_id in sorted(
        ids,
        reverse=True,
    ):
        await safe_delete_message(
            bot,
            chat_id,
            message_id,
        )


def get_remaining_today_count(
    manager,
):
    tasks = get_today_tasks(
        manager
    )

    return sum(
        1
        for task in tasks
        if str(
            task.get(
                "status",
                "",
            )
        ).strip()
        not in {
            "done",
            "postponed",
        }
    )


# =========================================================
# KEYBOARDS
# =========================================================

def client_actions_keyboard(
    task_row,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Связался",
                    callback_data=(
                        f"contact:{task_row}"
                    ),
                ),
                InlineKeyboardButton(
                    text="⏰ Отложить",
                    callback_data=(
                        f"postpone:{task_row}"
                    ),
                ),
            ]
        ]
    )


def result_keyboard(
    task_row,
):
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
            for code, title
            in RESULT_OPTIONS
        ]
    )


def next_contact_keyboard(
    task_row,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="7 дней",
                    callback_data=(
                        f"next:7:{task_row}"
                    ),
                ),
                InlineKeyboardButton(
                    text="14 дней",
                    callback_data=(
                        f"next:14:{task_row}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="30 дней",
                    callback_data=(
                        f"next:30:{task_row}"
                    ),
                ),
                InlineKeyboardButton(
                    text="60 дней",
                    callback_data=(
                        f"next:60:{task_row}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📅 Выбрать дату",
                    callback_data=(
                        f"next_custom:{task_row}"
                    ),
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
        if str(
            task.get(
                "status",
                "",
            )
        ).strip()
        not in {
            "done",
            "postponed",
        }
    ]

    if not active:
        total_clients = count_clients_for_manager(manager)

        if total_clients == 0:
            await bot.send_message(
                telegram_id,
                "⚠️ Для менеджера "
                f"<b>{manager}</b> в листе CLIENTS "
                "не найдено ни одного активного клиента.\n\n"
                "Повтори /register и укажи ФИО точно "
                "как в колонке <b>Оперативный менеджер</b>."
            )
        else:
            await bot.send_message(
                telegram_id,
                "✅ На сегодня клиентов для обязательного "
                "контакта нет.\n"
                f"В портфеле за вами закреплено: "
                f"<b>{total_clients}</b> клиентов."
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

    # CLIENTS читаем один раз для всей тройки.
    cws = clients_ws()
    cvalues = cws.get_all_values()

    states = {}

    if cvalues:
        cheaders = cvalues[0]

        for row_values in cvalues[1:]:
            row = row_to_dict(
                cheaders,
                row_values,
            )

            states[
                client_key(
                    row.get(
                        "client",
                        "",
                    ),
                    row.get(
                        "manager",
                        "",
                    ),
                )
            ] = row

    for index, task in enumerate(
        active,
        start=1,
    ):
        client = normalize_text(
            task.get(
                "client"
            )
        )

        state = states.get(
            client_key(
                client,
                manager,
            ),
            {},
        )

        card_message = await bot.send_message(
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
                int(
                    task[
                        "_row_num"
                    ]
                )
            ),
        )

        # Запоминаем ID карточки клиента,
        # чтобы после отработки убрать ее из чата.
        update_row_by_fields(
            tasks_ws(),
            int(
                task[
                    "_row_num"
                ]
            ),
            {
                "card_message_id": str(
                    card_message.message_id
                ),
            },
        )


# =========================================================
# HANDLERS
# =========================================================

def register_attention_handlers(
    dp,
    bot,
):
    @dp.message(CommandStart())
    async def start(
        message: Message,
    ):
        telegram_id = message.from_user.id

        manager = get_manager_by_telegram_id(
            telegram_id
        )

        if manager:
            await message.answer(
                "👋 <b>Бот «Внимание на клиента» подключен</b>\n\n"
                f"Вы зарегистрированы как: <b>{manager}</b>\n"
                "Настройка завершена ✅\n\n"
                "По вторникам, средам и четвергам "
                "бот будет присылать клиентов для контакта."
            )
            return

        await message.answer(
            "👋 <b>Бот «Внимание на клиента» подключен</b>\n\n"
            "Ваш Telegram ID:\n"
            f"<code>{telegram_id}</code>\n\n"
            "Пока ваш ID не привязан к менеджеру.\n"
            "Передайте этот ID руководителю — "
            "после привязки дополнительных действий не потребуется."
        )

    @dp.message(
        Command(
            "register"
        )
    )
    async def register(
        message: Message,
    ):
        manager = (
            message.text
            .replace(
                "/register",
                "",
                1,
            )
            .strip()
        )

        if not manager:
            await message.answer(
                "Укажи ФИО менеджера.\n"
                "Например:\n"
                "<code>/register Лилия Буглак</code>\n\n"
                "Обычным сотрудникам эта команда не нужна — "
                "привязку может заранее сделать руководитель."
            )

            return

        canonical_manager = resolve_manager_name_from_clients(
            manager
        )

        if not canonical_manager:
            await message.answer(
                "⚠️ Не нашла такого оперативного менеджера "
                "в загруженном портфеле.\n\n"
                "Введи ФИО так, как оно указано в колонке "
                "<b>Оперативный менеджер</b>."
            )
            return

        register_manager(
            canonical_manager,
            message.from_user.id,
        )

        await message.answer(
            "✅ Telegram привязан:\n"
            f"<b>{canonical_manager}</b>"
        )

    @dp.message(
        Command(
            "status"
        )
    )
    async def status(
        message: Message,
    ):
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
                "⚠️ Ваш Telegram пока не привязан к менеджеру.\n\n"
                "Ваш Telegram ID:\n"
                f"<code>{message.from_user.id}</code>\n\n"
                "Передайте этот ID руководителю. "
                "Самостоятельно регистрироваться не нужно."
            )

    @dp.message(
        Command(
            "today"
        )
    )
    async def today(
        message: Message,
    ):
        manager = get_manager_by_telegram_id(
            message.from_user.id
        )

        if not manager:
            await message.answer(
                "⚠️ Ваш Telegram пока не привязан к менеджеру.\n\n"
                "Ваш Telegram ID:\n"
                f"<code>{message.from_user.id}</code>\n\n"
                "Передайте этот ID руководителю. "
                "Самостоятельно регистрироваться не нужно."
            )

            return

        await send_today_tasks(
            bot,
            message.from_user.id,
            manager,
        )

    @dp.message(
        Command(
            "send_now"
        )
    )
    async def send_now(
        message: Message,
    ):
        if (
            REPORT_CHAT_ID
            and str(message.chat.id)
            != str(REPORT_CHAT_ID)
        ):
            await message.answer(
                "⚠️ Команда доступна только руководителю."
            )
            return

        # После команды можно указать ФИО, которые нужно исключить.
        # Например:
        # /send_now Буглак Лилия
        command_text = (
            message.text
            or ""
        ).strip()

        exclude_text = (
            command_text
            .replace(
                "/send_now",
                "",
                1,
            )
            .strip()
        )

        excluded = []

        if exclude_text:
            # Можно перечислить несколько ФИО через запятую.
            excluded = [
                normalize_text(item)
                for item in exclude_text.split(",")
                if normalize_text(item)
            ]

        def is_excluded(manager_name):
            for item in excluded:
                if manager_names_match(
                    manager_name,
                    item,
                ):
                    return True
            return False

        managers = get_active_managers()

        sent = []
        skipped = []
        failed = []

        await message.answer(
            "📤 Запускаю ручную рассылку..."
        )

        for item in managers:
            manager = item[
                "manager"
            ]

            if is_excluded(
                manager
            ):
                skipped.append(
                    f"{manager} — исключен вручную"
                )
                continue

            # Если этому менеджеру задания на сегодня уже создавались,
            # повторно не рассылаем.
            existing = get_today_tasks(
                manager
            )

            if existing:
                skipped.append(
                    f"{manager} — задания на сегодня уже есть"
                )
                continue

            try:
                await send_today_tasks(
                    bot,
                    item[
                        "telegram_id"
                    ],
                    manager,
                )

                sent.append(
                    manager
                )

            except Exception as e:
                traceback.print_exc()

                failed.append(
                    f"{manager}: {type(e).__name__}"
                )

        lines = [
            "✅ <b>Ручная рассылка завершена</b>",
            "",
            f"Отправлено: <b>{len(sent)}</b>",
            f"Пропущено: <b>{len(skipped)}</b>",
            f"Ошибок: <b>{len(failed)}</b>",
        ]

        if sent:
            lines.extend(
                [
                    "",
                    "<b>Получили:</b>",
                    *[
                        f"• {name}"
                        for name in sent
                    ],
                ]
            )

        if skipped:
            lines.extend(
                [
                    "",
                    "<b>Пропущены:</b>",
                    *[
                        f"• {item}"
                        for item in skipped
                    ],
                ]
            )

        if failed:
            lines.extend(
                [
                    "",
                    "<b>Не удалось отправить:</b>",
                    *[
                        f"• {item}"
                        for item in failed
                    ],
                    "",
                    "Если сотрудник еще ни разу не нажимал Start, "
                    "Telegram не позволит боту написать ему первым.",
                ]
            )

        await message.answer(
            "\n".join(
                lines
            )[:4000]
        )

    @dp.message(
        Command(
            "report"
        )
    )
    async def report(
        message: Message,
    ):
        # Если REPORT_CHAT_ID задан — отчет вручную доступен
        # только руководительскому чату.
        if (
            REPORT_CHAT_ID
            and str(message.chat.id)
            != str(REPORT_CHAT_ID)
        ):
            await message.answer(
                "⚠️ Отчет доступен только руководителю."
            )
            return

        await send_report(
            bot,
            message.chat.id,
        )

    @dp.message(
        Command(
            "debug_today"
        )
    )
    async def debug_today(
        message: Message,
    ):
        manager = get_manager_by_telegram_id(
            message.from_user.id
        )

        if not manager:
            await message.answer(
                "Сначала выполни /register ФИО"
            )
            return

        client_count = count_clients_for_manager(
            manager
        )

        existing_tasks = get_today_tasks(
            manager
        )

        await message.answer(
            "🔎 <b>Проверка выборки</b>\\n\\n"
            f"Менеджер: <b>{manager}</b>\\n"
            f"Клиентов в CLIENTS: <b>{client_count}</b>\\n"
            f"Заданий на сегодня: <b>{len(existing_tasks)}</b>"
        )

    @dp.callback_query(
        F.data.startswith(
            "contact:"
        )
    )
    async def contact(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        task_row = int(
            callback.data
            .split(":")[1]
        )

        task = get_task_by_row(
            task_row
        )

        if not task:
            await callback.answer(
                "Задание не найдено",
                show_alert=True,
            )

            return

        prompt_message = await callback.message.answer(
            (
                "Какой результат по клиенту "
                f"<b>{task.get('client')}</b>?"
            ),
            reply_markup=result_keyboard(
                task_row
            ),
        )

        # Сохраняем и исходную карточку клиента, и всю дальнейшую цепочку.
        # Поэтому после завершения карточка тоже гарантированно исчезает.
        await state.update_data(
            chain_message_ids=[
                callback.message.message_id,
                prompt_message.message_id,
            ]
        )

        await callback.answer()

    @dp.callback_query(
        F.data.startswith(
            "result:"
        )
    )
    async def result(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        _, result_code, task_row_text = (
            callback.data
            .split(
                ":",
                2,
            )
        )

        task_row = int(
            task_row_text
        )

        task = get_task_by_row(
            task_row
        )

        if not task:
            await callback.answer(
                "Задание не найдено",
                show_alert=True,
            )

            return

        manager = get_manager_by_telegram_id(
            callback.from_user.id
        )

        result_title = dict(
            RESULT_OPTIONS
        ).get(
            result_code,
            "Другое",
        )

        await remember_chain_message(
            state,
            callback.message.message_id,
        )

        await state.update_data(
            task_row=task_row,
            client=task.get(
                "client"
            ),
            manager=manager,
            result=result_title,
        )

        await state.set_state(
            ContactFlow.waiting_comment
        )

        prompt_message = await callback.message.answer(
            "Напиши короткий комментарий.\n"
            "Если не нужен — отправь <code>-</code>."
        )

        await remember_chain_message(
            state,
            prompt_message.message_id,
        )

        await callback.answer()

    @dp.message(
        ContactFlow.waiting_comment
    )
    async def comment(
        message: Message,
        state: FSMContext,
    ):
        data = await state.get_data()

        comment_text = (
            message.text
            or ""
        ).strip()

        if comment_text == "-":
            comment_text = ""

        await remember_chain_message(
            state,
            message.message_id,
        )

        await state.update_data(
            comment=comment_text
        )

        prompt_message = await message.answer(
            "Когда вернуться к клиенту?",
            reply_markup=next_contact_keyboard(
                int(
                    data[
                        "task_row"
                    ]
                )
            ),
        )

        await remember_chain_message(
            state,
            prompt_message.message_id,
        )

    @dp.callback_query(
        F.data.startswith(
            "next:"
        )
    )
    async def next_contact(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        _, days_text, task_row_text = (
            callback.data.split(
                ":",
                2,
            )
        )

        data = await state.get_data()

        next_date = (
            today_local()
            + timedelta(
                days=int(
                    days_text
                )
            )
        )

        task_row = int(
            task_row_text
        )

        save_communication(
            client=data[
                "client"
            ],
            manager=data[
                "manager"
            ],
            telegram_id=callback.from_user.id,
            result=data[
                "result"
            ],
            comment=data.get(
                "comment",
                "",
            ),
            next_contact=next_date,
            task_row=task_row,
        )

        await remember_chain_message(
            state,
            callback.message.message_id,
        )

        state_data = await state.get_data()

        await cleanup_client_chain(
            bot,
            callback.message.chat.id,
            task_row,
            state_data.get(
                "chain_message_ids",
                [],
            ),
        )

        manager = data[
            "manager"
        ]

        await state.clear()

        remaining = get_remaining_today_count(
            manager
        )

        if remaining == 0:
            await bot.send_message(
                callback.message.chat.id,
                "✅ <b>План на сегодня выполнен</b>\n"
                "Все назначенные клиенты обработаны."
            )

        await callback.answer()

    @dp.callback_query(
        F.data.startswith(
            "postpone:"
        )
    )
    async def postpone(
        callback: CallbackQuery,
        state: FSMContext,
    ):
        task_row = int(
            callback.data
            .split(":")[1]
        )

        task = get_task_by_row(
            task_row
        )

        if not task:
            await callback.answer(
                "Задание не найдено",
                show_alert=True,
            )

            return

        manager = get_manager_by_telegram_id(
            callback.from_user.id
        )

        await state.update_data(
            task_row=task_row,
            client=task.get(
                "client"
            ),
            manager=manager,
            # Сразу запоминаем исходную карточку клиента.
            chain_message_ids=[
                callback.message.message_id
            ],
        )

        await state.set_state(
            ContactFlow.waiting_postpone_reason
        )

        prompt_message = await callback.message.answer(
            "Почему откладываем? "
            "Напиши короткую причину."
        )

        await remember_chain_message(
            state,
            prompt_message.message_id,
        )

        await callback.answer()

    @dp.message(
        ContactFlow.waiting_postpone_reason
    )
    async def postpone_reason(
        message: Message,
        state: FSMContext,
    ):
        data = await state.get_data()

        reason = (
            message.text
            or ""
        ).strip()

        await remember_chain_message(
            state,
            message.message_id,
        )

        next_date = (
            today_local()
            + timedelta(
                days=14
            )
        )

        task_row = int(
            data[
                "task_row"
            ]
        )

        save_postpone(
            client=data[
                "client"
            ],
            manager=data[
                "manager"
            ],
            telegram_id=message.from_user.id,
            reason=reason,
            next_contact=next_date,
            task_row=task_row,
        )

        state_data = await state.get_data()

        await cleanup_client_chain(
            bot,
            message.chat.id,
            task_row,
            state_data.get(
                "chain_message_ids",
                [],
            ),
        )

        manager = data[
            "manager"
        ]

        await state.clear()

        remaining = get_remaining_today_count(
            manager
        )

        if remaining == 0:
            await bot.send_message(
                message.chat.id,
                "✅ <b>План на сегодня выполнен</b>\n"
                "Все назначенные клиенты обработаны."
            )


# =========================================================
# REPORT + SCHEDULER
# =========================================================

async def send_report(
    bot,
    chat_id,
):
    ws = tasks_ws()

    values = ws.get_all_values()

    today_str = today_local().strftime(
        "%Y-%m-%d"
    )

    if not values:
        await bot.send_message(
            chat_id,
            "📊 Сегодня заданий по коммуникации нет."
        )
        return

    headers = values[0]

    grouped = {}

    for row_values in values[1:]:
        row = row_to_dict(
            headers,
            row_values,
        )

        if (
            str(
                row.get(
                    "task_date",
                    "",
                )
            ).strip()
            != today_str
        ):
            continue

        manager = normalize_text(
            row.get(
                "manager"
            )
        )

        if not manager:
            continue

        grouped.setdefault(
            manager,
            {
                "total": 0,
                "contacted": 0,
                "postponed": 0,
                "pending": [],
                "postponed_clients": [],
            },
        )

        grouped[
            manager
        ][
            "total"
        ] += 1

        status = str(
            row.get(
                "status",
                "",
            )
        ).strip()

        client = normalize_text(
            row.get(
                "client"
            )
        )

        if status == "done":
            grouped[
                manager
            ][
                "contacted"
            ] += 1

        elif status == "postponed":
            grouped[
                manager
            ][
                "postponed"
            ] += 1

            if client:
                grouped[
                    manager
                ][
                    "postponed_clients"
                ].append(
                    client
                )

        else:
            if client:
                grouped[
                    manager
                ][
                    "pending"
                ].append(
                    client
                )

    if not grouped:
        await bot.send_message(
            chat_id,
            "📊 Сегодня задания менеджерам еще не создавались."
        )
        return

    total_assigned = sum(
        info["total"]
        for info in grouped.values()
    )

    total_contacted = sum(
        info["contacted"]
        for info in grouped.values()
    )

    total_postponed = sum(
        info["postponed"]
        for info in grouped.values()
    )

    total_pending = sum(
        len(info["pending"])
        for info in grouped.values()
    )

    lines = [
        (
            "📊 <b>Контроль коммуникации · "
            f"{today_local().strftime('%d.%m')}</b>"
        ),
        "",
        (
            f"Назначено: <b>{total_assigned}</b> · "
            f"Связались: <b>{total_contacted}</b> · "
            f"Отложили: <b>{total_postponed}</b> · "
            f"Не обработано: <b>{total_pending}</b>"
        ),
        "",
        "<b>По менеджерам:</b>",
    ]

    for manager, info in sorted(
        grouped.items(),
        key=lambda x: x[0].lower(),
    ):
        completed = (
            info["contacted"]
            + info["postponed"]
        )

        icon = (
            "✅"
            if completed == info["total"]
            else "⚠️"
        )

        lines.append(
            f"{icon} <b>{manager}</b> — "
            f"{completed}/{info['total']} "
            f"(связались {info['contacted']}, "
            f"отложили {info['postponed']})"
        )

    pending_lines = []

    for manager, info in grouped.items():
        for client in info["pending"]:
            pending_lines.append(
                f"• {client} — {manager}"
            )

    if pending_lines:
        lines.extend(
            [
                "",
                "<b>Не обработаны:</b>",
                *pending_lines,
            ]
        )

    # Telegram ограничивает длину сообщения.
    text = "\n".join(
        lines
    )

    if len(text) <= 4000:
        await bot.send_message(
            chat_id,
            text,
        )
    else:
        # Основная сводка.
        await bot.send_message(
            chat_id,
            "\n".join(
                lines[:(
                    len(lines)
                    - len(pending_lines)
                    - (2 if pending_lines else 0)
                )]
            )[:4000],
        )

        # Необработанные — отдельными частями.
        if pending_lines:
            chunk = "<b>Не обработаны:</b>\n"

            for line in pending_lines:
                candidate = (
                    chunk
                    + line
                    + "\n"
                )

                if len(candidate) > 3900:
                    await bot.send_message(
                        chat_id,
                        chunk.rstrip(),
                    )
                    chunk = (
                        "<b>Не обработаны:</b>\n"
                        + line
                        + "\n"
                    )
                else:
                    chunk = candidate

            if chunk.strip():
                await bot.send_message(
                    chat_id,
                    chunk.rstrip(),
                )


async def start_attention_scheduler(
    bot,
):
    last_send = None
    last_report = None

    print(
        (
            "ATTENTION SCHEDULER STARTED: "
            f"days=Tue/Wed/Thu; "
            f"send={DAILY_SEND_TIME}; "
            f"report={REPORT_SEND_TIME}; "
            f"clients={CLIENTS_PER_DAY}"
        ),
        flush=True,
    )

    while True:
        try:
            now = now_local()
            hhmm = now.strftime(
                "%H:%M"
            )

            weekday = now.weekday()

            is_auto_day = (
                weekday
                in AUTO_SEND_WEEKDAYS
            )

            # ---------------------------------------------
            # Автоматическая выдача 3 клиентов:
            # только ВТ / СР / ЧТ.
            # ---------------------------------------------
            if (
                is_auto_day
                and hhmm
                == DAILY_SEND_TIME
                and last_send
                != now.date()
            ):
                managers = (
                    get_active_managers()
                )

                print(
                    (
                        "AUTO CLIENT SEND: "
                        f"{now.strftime('%d.%m.%Y')} "
                        f"managers={len(managers)}"
                    ),
                    flush=True,
                )

                for item in managers:
                    try:
                        await send_today_tasks(
                            bot,
                            item[
                                "telegram_id"
                            ],
                            item[
                                "manager"
                            ],
                        )

                    except Exception:
                        traceback.print_exc()

                last_send = now.date()

            # ---------------------------------------------
            # Вечерний отчет руководителю:
            # тоже только ВТ / СР / ЧТ.
            # ---------------------------------------------
            if (
                is_auto_day
                and REPORT_CHAT_ID
                and hhmm
                == REPORT_SEND_TIME
                and last_report
                != now.date()
            ):
                try:
                    await send_report(
                        bot,
                        int(
                            REPORT_CHAT_ID
                        ),
                    )

                except Exception:
                    traceback.print_exc()

                last_report = now.date()

        except Exception:
            traceback.print_exc()

        await asyncio.sleep(
            30
        )
