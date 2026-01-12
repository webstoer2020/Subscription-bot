import logging
from datetime import datetime, timedelta
from typing import List, Dict
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from telegram.constants import ParseMode

from database import Database
from config import Config

logger = logging.getLogger(__name__)

class NotificationScheduler:
    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone(Config.TIMEZONE))
        self.config = Config

    async def start(self):
        """بدء المهام المجدولة"""
        # ملاحظة: تم إزالة daily_check لأنها تستدعي دالة غير موجودة
        # الإشعارات تضاف عند إنشاء الاشتراك في دالة add_subscriber في database.py

        # جدولة فحص الإشعارات كل 10 ثوانٍ (توازن بين السرعة والأداء)
        self.scheduler.add_job(
            self.check_notifications,
            'interval',
            seconds=10,
            id="check_notifications"
        )

        # جدولة فحص الاشتراكات المنتهية كل 30 ثانية
        self.scheduler.add_job(
            self.check_expired_subscriptions,
            'interval',
            seconds=30,
            id="check_expired",
            replace_existing=True
        )

        self.scheduler.start()
        logger.info("✅ Scheduler started successfully")

    async def check_notifications(self):
        """فحص الإشعارات المعلقة وإرسالها"""
        pending_notifications = self.db.get_pending_notifications()

        for notification in pending_notifications:
            try:
                user_id = notification['user_id']
                notification_type = notification['notification_type']

                # الحصول على بيانات المشترك
                subscriber = self.db.get_subscriber(user_id)
                if not subscriber:
                    continue

                from utils import format_date
                expiry_display = format_date(subscriber['subscription_end'])

                # إعداد نص الإشعار
                if notification_type in self.config.MESSAGES['notification']:
                    message = self.config.MESSAGES['notification'][notification_type].format(
                        expiry_date=expiry_display
                    )
                else:
                    # Fallback for old types or 0 minutes
                    if notification_type == "0_days" or notification_type == "0_minutes":
                         message = self.config.MESSAGES['notification']['0_days'].format(
                            expiry_date=expiry_display
                        )
                    else:
                        continue

                # إرسال الإشعار
                await self.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )

                # تحديث حالة الإشعار
                self.db.mark_notification_sent(notification['id'])

                logger.info(f"✅ Notification sent to user {user_id}: {notification_type}")

            except Exception as e:
                logger.error(f"❌ Error sending notification: {e}")

    async def check_expired_subscriptions(self):
        """فحص الاشتراكات المنتهية وطرد المستخدمين"""
        # الحصول على جميع المشتركين النشطين لفحصهم بدقة
        active_subscribers = self.db.get_all_subscribers(status='active')
        now = datetime.now(pytz.timezone(self.config.TIMEZONE))

        for subscriber in active_subscribers:
            try:
                user_id = subscriber['user_id']
                subscription_end = subscriber['subscription_end']

                # التأكد من أن التاريخ بتنسيق datetime
                if isinstance(subscription_end, str):
                    subscription_end = datetime.fromisoformat(subscription_end)

                # فحص ما إذا كان الاشتراك قد انتهى فعلياً
                if subscription_end <= now:
                    # طرد المستخدم من القناة
                    from utils import kick_user_from_channel
                    success = await kick_user_from_channel(
                        self.bot,
                        user_id,
                        self.config.CHANNEL_ID
                    )

                    if success:
                        # تحديث حالة المشترك
                        self.db.update_subscriber_status(user_id, 'expired')

                        # إرسال رسالة انتهاء الاشتراك
                        renew_msg = f"{self.config.MESSAGES['expired_kick_message']}\n\n{self.config.MESSAGES['renew_subscription']}"
                        await self.bot.send_message(
                            chat_id=user_id,
                            text=renew_msg,
                            parse_mode=ParseMode.HTML
                        )

                        logger.info(f"✅ User {user_id} kicked from channel and notified")
                    else:
                        logger.error(f"❌ Failed to kick user {user_id} from channel")

            except Exception as e:
                logger.error(f"❌ Error processing expired subscription for user {subscriber['user_id']}: {e}")

    async def stop(self):
        """إيقاف المخطط"""
        self.scheduler.shutdown()
        logger.info("⏹️ Scheduler stopped")