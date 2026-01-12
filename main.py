import logging
import asyncio
from telegram.ext import Application
from telegram import BotCommand

from database import Database
from handlers import SubscriptionHandlers
from scheduler import NotificationScheduler
from config import Config

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # التحقق من وجود التوكن
    if not Config.BOT_TOKEN:
        logger.error("❌ لم يتم تعيين TELEGRAM_TOKEN أو BOT_TOKEN في متغيرات البيئة")
        return

    if not Config.CHANNEL_ID:
        logger.error("❌ لم يتم تعيين CHANNEL_ID في متغيرات البيئة")
        return

    if not Config.ADMIN_IDS:
        logger.warning("⚠️ لم يتم تعيين ADMIN_IDS في متغيرات البيئة، البوت سيعمل لكن دون إمكانيات الإدارة")

    # تهيئة قاعدة البيانات
    try:
        db = Database(timezone=Config.TIMEZONE)
        logger.info("✅ قاعدة البيانات جاهزة")
    except Exception as e:
        logger.error(f"❌ فشل تهيئة قاعدة البيانات: {e}")
        return

    # إنشاء تطبيق البوت
    try:
        application = Application.builder().token(Config.BOT_TOKEN).build()
        bot = application.bot
        logger.info("✅ تطبيق البوت جاهز")
    except Exception as e:
        logger.error(f"❌ فشل إنشاء تطبيق البوت: {e}")
        return

    # التحقق من صلاحيات البوت في القناة (للتحذير فقط)
    try:
        chat_member = await bot.get_chat_member(Config.CHANNEL_ID, bot.id)
        if chat_member.status not in ['administrator', 'creator']:
            logger.warning(f"⚠️ البوت ليس مشرفاً في القناة {Config.CHANNEL_ID} - بعض المهام قد لا تعمل")
        else:
            logger.info("✅ البوت مشرف في القناة")
    except Exception as e:
        logger.warning(f"⚠️ لا يمكن التحقق من صلاحيات البوت في القناة: {e}")

    # تهيئة المعالجات
    handlers = SubscriptionHandlers(db, bot)
    handlers.register_handlers(application)

    # ضبط أزرار القائمة الثابتة (Menu Commands)
    await bot.set_my_commands([
        BotCommand("start", "بدء استخدام البوت")
    ])

    # تهيئة المجدول
    scheduler = NotificationScheduler(bot, db)
    await scheduler.start()
    logger.info("✅ المجدول يعمل")

    # بدء البوت
    logger.info("🚀 بدء تشغيل البوت...")

    # تشغيل البوت
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        # Keep the bot running until interrupted
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if str(e) == "This event loop is already running":
            # Fallback for environments where an event loop is already running (like Replit)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(main())
            else:
                loop.run_until_complete(main())
        else:
            raise e