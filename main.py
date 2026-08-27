import os
import asyncio
import traceback

from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from attention_bot import (
    register_attention_handlers,
    start_attention_scheduler,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher(storage=MemoryStorage())


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


async def main():
    print(
        "ATTENTION BOT STARTING",
        flush=True,
    )

    register_attention_handlers(
        dp=dp,
        bot=bot,
    )

    await start_web_app()

    asyncio.create_task(
        start_attention_scheduler(bot)
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    print(
        "BOT POLLING STARTING",
        flush=True,
    )

    try:
        await dp.start_polling(bot)
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
