import os
import json
import asyncio
import gspread
import traceback
from google.oauth2.service_account import Credentials
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dashboard_generator import build_dashboard
from datetime import datetime

TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise RuntimeError('Не задан BOT_TOKEN')

# Для Render можно указать публичную ссылку сервиса, например:
# DASHBOARD_URL=https://invctc-dashboard.onrender.com/dashboard
PUBLIC_DASHBOARD_URL = os.getenv('DASHBOARD_URL', '').strip()
PORT = int(os.getenv('PORT', '10000'))

BASE = Path(__file__).resolve().parent
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
UPLOADS = BASE / 'uploads'
UPLOADS.mkdir(exist_ok=True)
OUTPUT_DIR = BASE / 'output'
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT = OUTPUT_DIR / 'dashboard.html'
def get_storage_sheet():
    if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_SHEET_ID:
        raise RuntimeError("Не заданы GOOGLE_CREDENTIALS_JSON или GOOGLE_SHEET_ID")

    info = json.loads(GOOGLE_CREDENTIALS_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)

    return client.open_by_key(GOOGLE_SHEET_ID)


def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))

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
        if row.get("kind") == kind:
            row_index = i
            break

    data = [kind, file_id, filename, now]

    if row_index:
        ws.update(f"A{row_index}:D{row_index}", [data])
    else:
        ws.append_row(data)


def save_snooze_to_storage(client, manager, until, reason=""):
    spreadsheet = get_storage_sheet()

    ws = get_or_create_worksheet(
        spreadsheet,
        "SNOOZE",
        ["client", "manager", "until", "reason", "created_at"]
    )

    rows = ws.get_all_records()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    row_index = None

    for i, row in enumerate(rows, start=2):
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

        result = {}

        for row in ws.get_all_records():
            client = str(row.get("client", "")).strip()

            if client:
                result[client] = {
                    "manager": row.get("manager", ""),
                    "until": row.get("until", ""),
                    "reason": row.get("reason", row.get("comment", "")),
                    "created_at": row.get("created_at", "")
                }

        return result

    except Exception:
        print("Не удалось сохранить file_id в Google Sheets:", flush=True)
        traceback.print_exc()
        return {}
SNOOZE_FILE = OUTPUT_DIR / "snoozed_clients.json"

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
user_files = {}

REQUIRED = {
    'orders': 'заказы',
    'requests': 'запросы',
    'portfolio': 'портфель'
}

def detect_kind(filename: str, caption: str = ''):
    text = (filename + ' ' + (caption or '')).lower()
    if 'order' in text or 'заказ' in text:
        return 'orders'
    if 'crm' in text or 'запрос' in text:
        return 'requests'
    if 'list_company' in text or 'портфель' in text or 'клиент' in text:
        return 'portfolio'
    return None

def dashboard_keyboard():
    buttons = []
    if PUBLIC_DASHBOARD_URL:
        buttons.append([InlineKeyboardButton(text='Открыть дашборд', url=PUBLIC_DASHBOARD_URL)])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
def load_snoozed_clients():
    if not SNOOZE_FILE.exists():
        return {}

    try:
        return json.loads(
            SNOOZE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


def save_snoozed_clients(data):
    SNOOZE_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )
@dp.message(CommandStart())
async def start(message: Message):
    user_files[message.from_user.id] = {}
    await message.answer(
        'Пришлите 3 Excel-файла: <b>заказы</b>, <b>запросы</b>, <b>клиентский портфель</b>. Можно по одному файлу.\n\n'
        'Если бот не поймет тип файла, отправьте файл с подписью: <b>заказы</b>, <b>запросы</b> или <b>портфель</b>.'
    )

@dp.message(Command('dashboard'))
async def dashboard(message: Message):
    if OUTPUT.exists():
        await message.answer_document(
            FSInputFile(OUTPUT),
            caption='Текущая версия дашборда ✅',
            reply_markup=dashboard_keyboard()
        )
    else:
        await message.answer('Дашборд еще не создан. Пришлите 3 Excel-файла: заказы, запросы, портфель.')

@dp.message(Command('reset'))
async def reset(message: Message):
    user_files[message.from_user.id] = {}
    await message.answer('Файлы сброшены. Пришлите заново 3 Excel-файла.')

@dp.message(F.document)
async def doc(message: Message):
    uid = message.from_user.id
    user_files.setdefault(uid, {})

    filename = message.document.file_name or 'file.xlsx'
    kind = detect_kind(filename, message.caption or '')

    if not kind:
        await message.answer(
            'Не поняла тип файла. В подписи напишите: '
            '<b>заказы</b> / <b>запросы</b> / <b>портфель</b>.'
        )
        return

    path = UPLOADS / f'{uid}_{kind}_{filename}'
    await bot.download(message.document, destination=path)

    user_files[uid][kind] = str(path)

    try:
        save_uploaded_file_to_storage(
            kind=kind,
            file_id=message.document.file_id,
            filename=filename
        )
    except Exception as e:
        print(f"Не удалось сохранить file_id в Google Sheets: {e}", flush=True)

    missing = [v for k, v in REQUIRED.items() if k not in user_files[uid]]

    if missing:
        await message.answer(
            f'Файл <b>{REQUIRED[kind]}</b> принят. '
            f'Осталось прислать: {", ".join(missing)}.'
        )
        return

    try:
        snoozed_clients = load_snoozed_clients()
        
        print(f"SNOOZE LOADED: {len(snoozed_clients)}", flush=True)
        
        build_dashboard(
            order_path=user_files[uid]['orders'],
            requests_path=user_files[uid]['requests'],
            portfolio_path=user_files[uid]['portfolio'],
            output_path=str(OUTPUT),
            snoozed_clients=snoozed_clients
        )

        await message.answer_document(
            FSInputFile(OUTPUT),
            caption='Дашборд обновлен ✅',
            reply_markup=dashboard_keyboard()
        )

    except Exception as e:
        await message.answer(f'Не удалось собрать дашборд: <code>{e}</code>')


async def snooze_client(request):
    try:
        data = await request.json()

        client = str(data.get("client", "")).strip()
        until = str(data.get("until", "")).strip()
        manager = str(data.get("manager", "")).strip()
        reason = str(data.get("reason", "")).strip()

        if not client or not until:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Не указан клиент или дата"
                },
                status=400
            )

        save_snooze_to_storage(
            client=client,
            manager=manager,
            until=until,
            reason=reason
        )

        print(f"SNOOZED: {client} до {until}", flush=True)

        return web.json_response({"ok": True})

    except Exception:
        traceback.print_exc()
        return web.json_response(
            {
                "ok": False,
                "error": "Ошибка сохранения в Google Sheets. Подробности в Render Logs."
            },
            status=500
        )


async def health(request):
    return web.Response(text='OK')


async def dashboard_page(request):
    if not OUTPUT.exists():
        return web.Response(
            text='<h1>Дашборд еще не создан</h1><p>Загрузите 3 Excel-файла в Telegram-бот.</p>',
            content_type='text/html',
            charset='utf-8'
        )

    return web.FileResponse(OUTPUT)


async def start_web_app():
    app = web.Application()

    app.router.add_get('/', dashboard_page)
    app.router.add_get('/dashboard', dashboard_page)
    app.router.add_get('/health', health)
    app.router.add_post('/snooze', snooze_client)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()


@dp.message(Command("chatid"))
async def chatid(message: Message):
    await message.answer(
        f"Chat ID: <code>{message.chat.id}</code>\n"
        f"Type: <code>{message.chat.type}</code>"
    )


@dp.message()
async def debug_all(message: Message):
    print(
        f"CHAT={message.chat.id} "
        f"TYPE={message.chat.type} "
        f"TEXT={message.text}",
        flush=True
    )


async def main():
    print("WEB APP STARTING", flush=True)
    await start_web_app()
    print("WEB APP STARTED", flush=True)

    try:
        print("BOT POLLING STARTING", flush=True)
        await dp.start_polling(bot)
    except Exception as e:
        print(f"BOT POLLING ERROR: {e}", flush=True)
        raise


if __name__ == '__main__':
    asyncio.run(main())
