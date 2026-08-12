import os
import json
import asyncio
import traceback
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from dashboard_generator import build_dashboard


TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

PUBLIC_DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").strip()
PORT = int(os.getenv("PORT", "10000"))

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
UPLOADS.mkdir(exist_ok=True)

OUTPUT_DIR = BASE / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

OUTPUT = OUTPUT_DIR / "dashboard.html"

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

user_files = {}

REQUIRED_FILES = {
    "orders": "заказы",
    "requests": "запросы",
    "portfolio": "портфель",
}


def detect_kind(filename: str, caption: str = ""):
    text = f"{filename} {caption}".lower()

    if "crm" in text or "request" in text or "запрос" in text:
        return "requests"

    if (
        "portfolio" in text
        or "портфель" in text
        or "list_company" in text
        or "клиент" in text
    ):
        return "portfolio"

    if "order" in text or "заказ" in text:
        return "orders"

    return None


def dashboard_keyboard():
    if not PUBLIC_DASHBOARD_URL:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть дашборд", url=PUBLIC_DASHBOARD_URL)]
        ]
    )


def get_storage_sheet():
    if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_SHEET_ID:
        raise RuntimeError("Не заданы GOOGLE_CREDENTIALS_JSON или GOOGLE_SHEET_ID")

    info = json.loads(GOOGLE_CREDENTIALS_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID)


def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=title,
            rows=1000,
            cols=max(len(headers), 5)
        )

    values = ws.get_all_values()

    if not values:
        ws.append_row(headers)

    return ws


def save_uploaded_file_to_storage(kind, file_id, filename):
    spreadsheet = get_storage_sheet()

    ws = get_or_create_worksheet(
        spreadsheet,
        "FILES",
        ["kind", "file_id", "filename", "updated_at"]
    )

    rows = ws.get_all_records()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    row_index = None

    for i, row in enumerate(rows, start=2):
        if str(row.get("kind", "")).strip() == kind:
            row_index = i
            break

    data = [kind, file_id, filename, now]

    if row_index:
        ws.update(f"A{row_index}:D{row_index}", [data])
    else:
        ws.append_row(data)


def load_saved_files():
    try:
        spreadsheet = get_storage_sheet()

        ws = get_or_create_worksheet(
            spreadsheet,
            "FILES",
            ["kind", "file_id", "filename", "updated_at"]
        )

        result = {}

        for row in ws.get_all_records():
            kind = str(row.get("kind", "")).strip()
            file_id = str(row.get("file_id", "")).strip()
            filename = str(row.get("filename", "")).strip()

            if kind in REQUIRED_FILES and file_id:
                result[kind] = {
                    "file_id": file_id,
                    "filename": filename or f"{kind}.xlsx",
                }

        return result

    except Exception:
        traceback.print_exc()
        return {}


def save_snooze_to_storage(client, manager, until, reason=""):
    spreadsheet = get_storage_sheet()

    ws = get_or_create_worksheet(
        spreadsheet,
        "SNOOZE",
        ["client", "manager", "until", "reason", "created_at"]
    )

    raw = ws.get_all_values()

    if not raw:
        ws.append_row(["client", "manager", "until", "reason", "created_at"])
        raw = ws.get_all_values()

    headers = [h.strip().lower() for h in raw[0]]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    row_index = None

    for i, row_values in enumerate(raw[1:], start=2):
        row = dict(zip(headers, row_values))

        if str(row.get("client", "")).strip() == client:
            row_index = i
            break

    data = [client, manager, until, reason, now]

    if row_index:
        ws.update(f"A{row_index}:E{row_index}", [data])
    else:
        ws.append_row(data)


def load_snoozed_clients():
    try:
        spreadsheet = get_storage_sheet()

        ws = get_or_create_worksheet(
            spreadsheet,
            "SNOOZE",
            ["client", "manager", "until", "reason", "created_at"]
        )

        raw = ws.get_all_values()
        result = {}

        if len(raw) < 2:
            print("SNOOZE PARSED: 0", flush=True)
            return result

        headers = [h.strip().lower() for h in raw[0]]

        for row_values in raw[1:]:
            row = dict(zip(headers, row_values))

            client = str(row.get("client", "")).strip()
            if not client:
                continue

            result[client] = {
                "manager": row.get("manager", ""),
                "until": row.get("until", ""),
                "reason": row.get("reason", row.get("comment", "")),
                "created_at": row.get("created_at", ""),
            }

        print(f"SNOOZE PARSED: {len(result)}", flush=True)
        return result

    except Exception:
        traceback.print_exc()
        return {}


async def download_telegram_file(file_id: str, destination: Path):
    tg_file = await bot.get_file(file_id)
    await bot.download_file(tg_file.file_path, destination=destination)


def get_current_user_files(uid: int):
    return user_files.setdefault(uid, {})


def build_from_user_files(uid: int):
    files = user_files[uid]
    snoozed_clients = load_snoozed_clients()

    print(f"SNOOZE LOADED: {len(snoozed_clients)}", flush=True)

    build_dashboard(
        order_path=files["orders"],
        requests_path=files["requests"],
        portfolio_path=files["portfolio"],
        logistics_path=files["orders"],
        output_path=str(OUTPUT),
        snoozed_clients=snoozed_clients,
    )

    print(f"DASHBOARD SAVED TO: {OUTPUT}", flush=True)


async def rebuild_from_storage():
    saved = load_saved_files()

    missing = [kind for kind in REQUIRED_FILES if kind not in saved]

    if missing:
        print(f"REBUILD: не хватает файлов {missing}", flush=True)
        return False

    paths = {}

    for kind in REQUIRED_FILES:
        info = saved[kind]

        safe_filename = Path(
            info.get("filename") or f"{kind}.xlsx"
        ).name

        path = UPLOADS / f"saved_{kind}_{safe_filename}"

        await download_telegram_file(info["file_id"], path)
        paths[kind] = str(path)

    snoozed_clients = load_snoozed_clients()

    print(f"SNOOZE LOADED: {len(snoozed_clients)}", flush=True)

    build_dashboard(
        order_path=paths["orders"],
        requests_path=paths["requests"],
        portfolio_path=paths["portfolio"],
        logistics_path=paths["orders"],
        output_path=str(OUTPUT),
        snoozed_clients=snoozed_clients,
    )

    print(f"Дашборд восстановлен. OUTPUT={OUTPUT}", flush=True)
    return True


@dp.message(CommandStart())
async def start(message: Message):
    user_files[message.from_user.id] = {}

    await message.answer(
        "Пришлите 3 Excel-файла:\n"
        "1. <b>заказы</b>\n"
        "2. <b>запросы</b>\n"
        "3. <b>портфель</b>\n\n"
        "Раздел <b>Логистика</b> автоматически строится "
        "из файла <b>заказы</b>."
    )


@dp.message(Command("dashboard"))
async def dashboard(message: Message):
    if OUTPUT.exists():
        await message.answer(
            "Текущая версия дашборда ✅",
            reply_markup=dashboard_keyboard()
        )
    else:
        await message.answer(
            "Дашборд еще не создан. "
            "Пришлите 3 Excel-файла или выполните /rebuild."
        )


@dp.message(Command("rebuild"))
async def rebuild(message: Message):
    await message.answer("Пробую восстановить последний дашборд...")

    try:
        ok = await rebuild_from_storage()

        if ok:
            await message.answer(
                "Дашборд восстановлен ✅",
                reply_markup=dashboard_keyboard()
            )
        else:
            await message.answer(
                "Не удалось восстановить дашборд. "
                "Нужны сохраненные файлы: заказы, запросы, портфель."
            )

    except Exception as e:
        traceback.print_exc()
        await message.answer(f"Ошибка восстановления: <code>{e}</code>")


@dp.message(Command("reset"))
async def reset(message: Message):
    user_files[message.from_user.id] = {}
    await message.answer(
        "Текущая загрузка сброшена. Пришлите заново 3 файла."
    )


@dp.message(Command("chatid"))
async def chatid(message: Message):
    await message.answer(
        f"Chat ID: <code>{message.chat.id}</code>\n"
        f"Type: <code>{message.chat.type}</code>"
    )


@dp.message(F.document)
async def doc(message: Message):
    uid = message.from_user.id
    files = get_current_user_files(uid)

    filename = message.document.file_name or "file.xlsx"
    kind = detect_kind(filename, message.caption or "")

    if not kind:
        await message.answer(
            "Не поняла тип файла. "
            "Отправь его с подписью: "
            "<b>заказы</b> / <b>запросы</b> / <b>портфель</b>."
        )
        return

    safe_filename = Path(filename).name
    path = UPLOADS / f"{uid}_{kind}_{safe_filename}"

    await bot.download(message.document, destination=path)

    files[kind] = str(path)

    print(
        f"FILE RECEIVED: kind={kind}, filename={safe_filename}",
        flush=True
    )

    try:
        save_uploaded_file_to_storage(
            kind=kind,
            file_id=message.document.file_id,
            filename=safe_filename
        )
    except Exception:
        print(
            "Не удалось сохранить file_id в Google Sheets:",
            flush=True
        )
        traceback.print_exc()

    missing = [
        title
        for file_kind, title in REQUIRED_FILES.items()
        if file_kind not in files
    ]

    if missing:
        await message.answer(
            f"Файл <b>{REQUIRED_FILES[kind]}</b> принят. "
            f"Осталось прислать: {', '.join(missing)}."
        )
        return

    try:
        await message.answer(
            "Все 3 файла получены. Собираю дашборд..."
        )

        build_from_user_files(uid)

        await message.answer(
            "Дашборд обновлен ✅",
            reply_markup=dashboard_keyboard()
        )

    except Exception as e:
        traceback.print_exc()

        await message.answer(
            f"Не удалось собрать дашборд: <code>{e}</code>"
        )


async def snooze_client(request):
    try:
        data = await request.json()

        client = str(data.get("client", "")).strip()
        until = str(data.get("until", "")).strip()
        manager = str(data.get("manager", "")).strip()
        reason = str(data.get("reason", "")).strip()

        if not client or not until:
            return web.json_response(
                {"ok": False, "error": "Не указан клиент или дата"},
                status=400
            )

        save_snooze_to_storage(
            client=client,
            manager=manager,
            until=until,
            reason=reason
        )

        print(
            f"SNOOZED: {client} до {until}, причина: {reason}",
            flush=True
        )

        return web.json_response({"ok": True})

    except Exception:
        traceback.print_exc()

        return web.json_response(
            {
                "ok": False,
                "error": (
                    "Ошибка сохранения в Google Sheets. "
                    "Подробности в Render Logs."
                )
            },
            status=500
        )


async def health(request):
    return web.Response(text="OK")


async def dashboard_page(request):
    if not OUTPUT.exists():
        return web.Response(
            text=(
                "<h1>Дашборд еще не создан</h1>"
                "<p>Загрузите 3 Excel-файла "
                "в Telegram-бот или выполните /rebuild.</p>"
            ),
            content_type="text/html",
            charset="utf-8"
        )

    return web.FileResponse(OUTPUT)


async def start_web_app():
    app = web.Application()

    app.router.add_get("/", dashboard_page)
    app.router.add_get("/dashboard", dashboard_page)
    app.router.add_get("/health", health)
    app.router.add_post("/snooze", snooze_client)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()


async def main():
    print("WEB APP STARTING", flush=True)

    await start_web_app()

    print("WEB APP STARTED", flush=True)

    await bot.delete_webhook(drop_pending_updates=True)

    print("WEBHOOK CLEARED", flush=True)

    try:
        await rebuild_from_storage()
    except Exception:
        print(
            "Не удалось восстановить дашборд при старте:",
            flush=True
        )
        traceback.print_exc()

    print("BOT POLLING STARTING", flush=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
