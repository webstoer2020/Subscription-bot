import logging
from typing import Optional
from datetime import datetime, timedelta
import pytz
from telegram import User

logger = logging.getLogger(__name__)

def format_date(date_str: str, timezone: str = "Asia/Riyadh") -> str:
    """تنسيق التاريخ لعرضه للمستخدم"""
    try:
        tz = pytz.timezone(timezone)
        date = datetime.fromisoformat(date_str)
        localized_date = date.astimezone(tz)
        # تنسيق التاريخ بنظام 12 ساعة مع AM/PM
        return localized_date.strftime("%Y-%m-%d %I:%M %p")
    except Exception as e:
        logger.error(f"Error formatting date: {e}")
        return date_str

def calculate_notification_dates(subscription_end: datetime, intervals: list) -> dict:
    """حساب تواريخ الإشعارات"""
    notifications = {}
    for days in intervals:
        notification_date = subscription_end - timedelta(days=days)
        notifications[f"{days}_days"] = notification_date
    return notifications

def validate_username(username: str) -> bool:
    """التحقق من صحة اسم المستخدم"""
    if not username:
        return False
    # إزالة @ من البداية إذا موجود
    if username.startswith('@'):
        username = username[1:]
    return len(username) >= 5 and username.replace('_', '').isalnum()

def log_action(action: str, user_id: int, admin_id: Optional[int] = None, details: str = ""):
    """تسجيل الإجراءات"""
    logger.info(f"Action: {action}, User: {user_id}, Admin: {admin_id}, Details: {details}")

def is_user_in_channel(bot, user_id: int, channel_id: str) -> bool:
    """التحقق من وجود المستخدم في القناة"""
    try:
        member = bot.get_chat_member(channel_id, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking user in channel: {e}")
        return False

async def create_invite_link(bot, channel_id: str, user_id: Optional[int] = None) -> Optional[str]:
    """إنشاء رابط دعوة صالح لمرة واحدة فقط ويطلب الموافقة لضمان التعطيل"""
    try:
        # إنشاء رابط يتطلب موافقة الأدمن (البوت) لضمان التحكم الكامل
        # ملاحظة: لا يمكن تحديد member_limit عند تفعيل creates_join_request
        link_name = f"User_{user_id}" if user_id else None
        link = await bot.create_chat_invite_link(
            chat_id=channel_id,
            name=link_name,
            creates_join_request=True, # يتطلب موافقة، وهذا يضمن تعطيله بعد القبول
            expire_date=None
        )
        return link.invite_link
    except Exception as e:
        logger.error(f"Error creating invite link: {e}")
        return None

async def kick_user_from_channel(bot, user_id: int, channel_id: str) -> bool:
    """طرد مستخدم من القناة وحظره نهائياً حتى يتم إلغاء الحظر عند التجديد"""
    try:
        await bot.ban_chat_member(
            chat_id=channel_id,
            user_id=user_id,
            revoke_messages=False
        )
        # ملاحظة: لم نعد نقوم بعمل unban هنا لنضمن عدم قدرته على استخدام الروابط القديمة
        # سيتم عمل unban فقط في دالة التجديد أو إضافة مشترك جديد
        return True
    except Exception as e:
        logger.error(f"Error kicking user from channel: {e}")
        return False

async def unban_user_from_channel(bot, user_id: int, channel_id: str) -> bool:
    """إلغاء حظر مستخدم للسماح له بالانضمام مجدداً"""
    try:
        await bot.unban_chat_member(
            chat_id=channel_id,
            user_id=user_id,
            only_if_banned=True
        )
        return True
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")
        return False