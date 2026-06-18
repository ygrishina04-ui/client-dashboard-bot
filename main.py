import os
import asyncio
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dashboard_generator import build_dashboard

TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise RuntimeError('Не задан BOT_TOKEN')

# Для Render можно указать публичную ссылку сервиса, например:
# DASHBOARD_URL=https://invctc-dashboard.onrender.com/dashboard
PUBLIC_DASHBOARD_URL = os.getenv('DASHBOARD_URL', '').strip()
PORT = int(os.getenv('PORT', '10000'))

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / 'uploads'
UPLOADS.mkdir(exist_ok=True)
OUTPUT_DIR = BASE / 'output'
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT = OUTPUT_DIR / 'dashboard.html'

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
        await message.answer('Не поняла тип файла. В подписи напишите: <b>заказы</b> / <b>запросы</b> / <b>портфель</b>.')
        return

    path = UPLOADS / f'{uid}_{kind}_{filename}'
    await bot.download(message.document, destination=path)
    user_files[uid][kind] = str(path)

    missing = [v for k, v in REQUIRED.items() if k not in user_files[uid]]
    if missing:
        await message.answer(f'Файл <b>{REQUIRED[kind]}</b> принят. Осталось прислать: {", ".join(missing)}.')
        return

    try:
        build_dashboard(
            order_path=user_files[uid]['orders'],
            requests_path=user_files[uid]['requests'],
            portfolio_path=user_files[uid]['portfolio'],
            output_path=str(OUTPUT)
        )
        await message.answer_document(
            FSInputFile(OUTPUT),
            caption='Дашборд обновлен ✅',
            reply_markup=dashboard_keyboard()
        )
    except Exception as e:
        await message.answer(f'Не удалось собрать дашборд: <code>{e}</code>')

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
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

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
