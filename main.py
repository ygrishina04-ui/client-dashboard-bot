import os
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from dashboard_service import (
    register_dashboard_handlers,
    start_web_app,
    rebuild_from_storage,
)
from attention_bot import (
    register_attention_handlers,
    start_attention_scheduler,
    sync_portfolio_from_excel,
)


TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


async def main():
    print("APP STARTING", flush=True)

    # 1) Дашборд: загрузка 3 файлов, snooze, web-страница.
    register_dashboard_handlers(
        dp=dp,
        bot=bot,
        on_portfolio_uploaded=sync_portfolio_from_excel,
    )

    # 2) "Внимание на клиента": /register, /today, кнопки, отчеты.
    register_attention_handlers(
        dp=dp,
        bot=bot,
    )

    # 3) Web service Render.
    await start_web_app(bot)
    print("WEB APP STARTED", flush=True)

    # 4) Восстанавливаем последний дашборд после deploy/restart.
    try:
        await rebuild_from_storage(bot)
    except Exception:
        import traceback
        print("Не удалось восстановить дашборд при старте:", flush=True)
        traceback.print_exc()

    # 5) Автоматическая ежедневная рассылка 3 клиентов.
    asyncio.create_task(
        start_attention_scheduler(bot)
    )

    await bot.delete_webhook(drop_pending_updates=True)
    print("BOT POLLING STARTING", flush=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

