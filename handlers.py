import logging
from typing import Optional, Dict
from datetime import datetime, timedelta

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ChatJoinRequest
from telegram.ext import (
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes,
    Application,
    CallbackQueryHandler,
    ConversationHandler,
    ChatJoinRequestHandler
)
from telegram.constants import ParseMode

from database import Database
from utils import format_date, is_user_in_channel, kick_user_from_channel, unban_user_from_channel, log_action, create_invite_link
from config import Config

logger = logging.getLogger(__name__)

# States for ConversationHandler
GET_ID, CONFIRM_USER, GET_SUB_TYPE, GET_DAYS, GET_HOURS, GET_MINUTES, EDIT_USER_SELECT, EDIT_SUB_TYPE, EDIT_DAYS, EDIT_HOURS, EDIT_MINUTES = range(11)

class SubscriptionHandlers:
    def __init__(self, db: Database, bot: Bot):
        self.db = db
        self.bot = bot
        self.config = Config

    async def is_admin(self, user_id: int) -> bool:
        return user_id in self.config.ADMIN_IDS

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = user.id
        
        if await self.is_admin(user_id):
            keyboard = [
                [InlineKeyboardButton("➕ إضافة مشترك", callback_data='add_user_flow')],
                [InlineKeyboardButton("📋 قائمة المشتركين", callback_data='list_users_flow'), 
                 InlineKeyboardButton("🔴 المنتهيين", callback_data='list_expired_flow')],
                [InlineKeyboardButton("🔍 فحص سريع", callback_data='force_check'), 
                 InlineKeyboardButton("❓ مساعدة", callback_data='help')]
            ]
            await update.message.reply_text(self.config.MESSAGES['admin_welcome'], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            # إرسال بيانات المستخدم الجديد للأدمن لسهولة التفعيل
            admin_msg = (
                f"👤 <b>مستخدم جديد دخل البوت:</b>\n\n"
                f"📝 <b>الاسم:</b> {user.full_name}\n"
                f"🆔 <b>الآيدي:</b> <code>{user_id}</code>\n"
                f"👤 <b>اليوزر:</b> @{user.username if user.username else 'لا يوجد'}"
            )
            for admin_id in self.config.ADMIN_IDS:
                try:
                    await self.bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Error notifying admin {admin_id}: {e}")

            keyboard = [
                [InlineKeyboardButton("📊 فحص اشتراكي", callback_data='check_my_sub')]
            ]
            await update.message.reply_text(
                self.config.MESSAGES['welcome'],
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        return ConversationHandler.END

    async def check_my_sub_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        sub = self.db.get_subscriber(user_id)
        
        if sub and sub['status'] == 'active':
            from datetime import datetime
            import pytz
            # استخدام توقيت السعودية الفعلي للمقارنة
            tz = pytz.timezone("Asia/Riyadh")
            now = datetime.now(tz)
            
            expiry = sub['subscription_end']
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            
            # التأكد من أن وقت الانتهاء محدد بالمنطقة الزمنية الصحيحة
            if expiry.tzinfo is None:
                expiry = tz.localize(expiry)
            else:
                expiry = expiry.astimezone(tz)

            diff = expiry - now
            if diff.total_seconds() > 0:
                days = diff.days
                hours, remainder = divmod(diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                time_str = f"📅 {days} يوم\n⏱️ {hours} ساعة\n⏳ {minutes} دقيقة\n⚡ {seconds} ثانية"
                
                expiry_str = format_date(sub['subscription_end'])
                channel_link = self.config.CHANNEL_USERNAME if self.config.CHANNEL_USERNAME.startswith('http') else f"https://t.me/{self.config.CHANNEL_USERNAME.replace('@', '')}"
                
                # إضافة وقت التحديث لضمان حيوية البيانات
                last_update = now.strftime("%H:%M:%S")
                
                msg = (
                    f"✅ <b>اشتراكك فعال!</b>\n\n"
                    f"⏰ <b>ينتهي في:</b>\n<code>{expiry_str}</code>\n\n"
                    f"⏳ <b>الوقت المتبقي (تفاعلي):</b>\n{time_str}\n\n"
                    f"🕒 <b>آخر تحديث:</b> {last_update}\n"
                    f"🔗 <b>رابط القناة:</b> {channel_link}"
                )
                
                keyboard = [[InlineKeyboardButton("🔄 تحديث العداد (لحظي)", callback_data='check_my_sub')]]
                await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(self.config.MESSAGES['renew_subscription'], parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(self.config.MESSAGES['renew_subscription'], parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    async def add_user_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("الرجاء إرسال ID المستخدم أو @username المراد إضافته:")
        return GET_ID

    async def get_user_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        identifier = update.message.text.strip()
        try:
            if identifier.startswith('@'):
                # Handle username
                username = identifier[1:]
                # We try to get chat by username
                chat = await self.bot.get_chat(identifier)
                target_id = chat.id
            else:
                # Handle ID
                target_id = int(identifier)
                chat = await self.bot.get_chat(target_id)
            
            context.user_data['target_id'] = target_id
            context.user_data['target_name'] = chat.full_name
            context.user_data['target_username'] = chat.username or ""

            keyboard = [[InlineKeyboardButton("✅ تأكيد", callback_data='confirm_yes'), InlineKeyboardButton("❌ إلغاء", callback_data='confirm_no')]]
            await update.message.reply_text(self.config.MESSAGES['confirm_user'].format(name=chat.full_name, id=target_id), reply_markup=InlineKeyboardMarkup(keyboard))
            return CONFIRM_USER
        except Exception as e:
            await update.message.reply_text("❌ لم يتم العثور على المستخدم. تأكد من أن المستخدم قد بدأ المحادثة مع البوت أولاً أو أن المعرف صحيح:")
            return GET_ID

    async def confirm_user_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == 'confirm_yes':
            keyboard = [
                [InlineKeyboardButton("📅 بالأيام", callback_data='type_days'), 
                 InlineKeyboardButton("⏱️ بالساعات", callback_data='type_hours')],
                [InlineKeyboardButton("⏳ بالدقائق", callback_data='type_minutes')],
                [InlineKeyboardButton("❌ إلغاء", callback_data='confirm_no')]
            ]
            await query.edit_message_text("اختر نوع الاشتراك:", reply_markup=InlineKeyboardMarkup(keyboard))
            return GET_SUB_TYPE
        else:
            await query.edit_message_text("❌ تم إلغاء العملية.")
            return ConversationHandler.END

    async def get_sub_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == 'type_days':
            await query.edit_message_text(self.config.MESSAGES['enter_days'])
            return GET_DAYS
        elif query.data == 'type_hours':
            await query.edit_message_text("الرجاء إدخال عدد ساعات الاشتراك (رقم فقط):")
            return GET_HOURS
        elif query.data == 'type_minutes':
            await query.edit_message_text("الرجاء إدخال عدد دقائق الاشتراك (رقم فقط):")
            return GET_MINUTES
        return ConversationHandler.END

    async def get_subscription_days(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            days = int(update.message.text)
            target_id = context.user_data['target_id']
            
            # إلغاء الحظر أولاً للسماح له بالانضمام
            await unban_user_from_channel(self.bot, target_id, self.config.CHANNEL_ID)
            
            invite_link = await create_invite_link(self.bot, self.config.CHANNEL_ID, target_id)
            
            success = self.db.add_subscriber(
                user_id=target_id,
                username=context.user_data['target_username'],
                first_name=context.user_data['target_name'],
                last_name="",
                days=days
            )

            if success:
                sub = self.db.get_subscriber(target_id)
                msg = self.config.MESSAGES['user_added'].format(
                    username=context.user_data['target_name'], 
                    expiry_date=format_date(sub['subscription_end']),
                    invite_link=invite_link or "تعذر إنشاء رابط حالياً"
                )
                await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
                try:
                    await self.bot.send_message(
                        chat_id=target_id,
                        text=f"🎁 أهلاً بك! تم تفعيل اشتراكك لمدة {days} يوم.\n\nرابط الدخول للقناة (صالح لمرة واحدة):\n{invite_link}",
                        parse_mode=ParseMode.HTML
                    )
                except: pass
            else:
                await update.message.reply_text("❌ حدث خطأ في قاعدة البيانات.")
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال رقم صحيح:")
            return GET_DAYS

    async def get_subscription_hours(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            hours = int(update.message.text)
            target_id = context.user_data['target_id']
            
            # إلغاء الحظر أولاً
            await unban_user_from_channel(self.bot, target_id, self.config.CHANNEL_ID)
            
            invite_link = await create_invite_link(self.bot, self.config.CHANNEL_ID, target_id)
            
            success = self.db.add_subscriber(
                user_id=target_id,
                username=context.user_data['target_username'],
                first_name=context.user_data['target_name'],
                last_name="",
                days=0,
                hours=hours
            )

            if success:
                sub = self.db.get_subscriber(target_id)
                msg = self.config.MESSAGES['user_added'].format(
                    username=context.user_data['target_name'], 
                    expiry_date=format_date(sub['subscription_end']),
                    invite_link=invite_link or "تعذر إنشاء رابط حالياً"
                )
                await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
                try:
                    await self.bot.send_message(
                        chat_id=target_id,
                        text=f"🎁 أهلاً بك! تم تفعيل اشتراكك لمدة {hours} ساعة.\n\nرابط الدخول للقناة (صالح لمرة واحدة):\n{invite_link}",
                        parse_mode=ParseMode.HTML
                    )
                except: pass
            else:
                await update.message.reply_text("❌ حدث خطأ في قاعدة البيانات.")
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال رقم صحيح:")
            return GET_HOURS

    async def get_subscription_minutes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            minutes = int(update.message.text)
            target_id = context.user_data['target_id']
            
            # إلغاء الحظر أولاً
            await unban_user_from_channel(self.bot, target_id, self.config.CHANNEL_ID)
            
            invite_link = await create_invite_link(self.bot, self.config.CHANNEL_ID, target_id)
            
            success = self.db.add_subscriber(
                user_id=target_id,
                username=context.user_data['target_username'],
                first_name=context.user_data['target_name'],
                last_name="",
                days=0,
                hours=0,
                minutes=minutes
            )

            if success:
                sub = self.db.get_subscriber(target_id)
                msg = self.config.MESSAGES['user_added'].format(
                    username=context.user_data['target_name'], 
                    expiry_date=format_date(sub['subscription_end']),
                    invite_link=invite_link or "تعذر إنشاء رابط حالياً"
                )
                await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
                try:
                    await self.bot.send_message(
                        chat_id=target_id,
                        text=f"🎁 أهلاً بك! تم تفعيل اشتراكك لمدة {minutes} دقيقة.\n\nرابط الدخول للقناة (صالح لمرة واحدة):\n{invite_link}",
                        parse_mode=ParseMode.HTML
                    )
                except: pass
            else:
                await update.message.reply_text("❌ حدث خطأ في قاعدة البيانات.")
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال رقم صحيح:")
            return GET_MINUTES

    async def list_users_flow(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # تحديد الفلترة وتخزين نوع القائمة للرجوع إليها
        status_filter = None
        title = "قائمة المشتركين"
        if query.data == 'list_expired_flow':
            status_filter = 'expired'
            title = "المشتركين المنتهيين"
            context.user_data['last_list_type'] = 'expired'
        else:
            context.user_data['last_list_type'] = 'all'
            
        subscribers = self.db.get_all_subscribers(status=status_filter)
        if not subscribers:
            await query.edit_message_text(f"📭 لا يوجد مشتركين في {title}.")
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data='back_to_start')]]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            return EDIT_USER_SELECT

        response = f"📋 <b>{title} ({len(subscribers)})</b>\nإضغط على الاسم للتعديل أو التجديد:\n\n"
        keyboard = []
        for s in subscribers:
            status = "🟢" if s['status'] == 'active' else "🔴"
            keyboard.append([InlineKeyboardButton(f"{status} {s['first_name']} | @{s['username'] or 'بدون'}", callback_data=f"manage_{s['user_id']}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data='back_to_start')])
        await query.edit_message_text(response, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return EDIT_USER_SELECT

    async def manage_user_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data_parts = query.data.split('_')
        if len(data_parts) < 2:
            return
        
        user_id = int(data_parts[1])
        sub = self.db.get_subscriber(user_id)
        if not sub:
            await query.edit_message_text("❌ لم يتم العثور على بيانات المشترك.")
            return ConversationHandler.END
            
        context.user_data['manage_id'] = user_id
        
        status_text = "🟢 نشط" if sub['status'] == 'active' else "🔴 منتهي"
        
        # حساب الوقت المتبقي بالتفصيل
        from datetime import datetime
        import pytz
        now = datetime.now(pytz.timezone(self.config.TIMEZONE))
        expiry = sub['subscription_end']
        
        # التأكد من أن التاريخ بتنسيق datetime
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry)
        
        diff = expiry - now
        
        if diff.total_seconds() > 0:
            days = diff.days
            hours, remainder = divmod(diff.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            remaining_str = f"{days} يوم، {hours} ساعة، {minutes} دقيقة"
        else:
            remaining_str = "منتهي 🔴"

        list_callback = 'list_expired_flow' if context.user_data.get('last_list_type') == 'expired' else 'list_users_flow'
        keyboard = [
            [InlineKeyboardButton("🔄 تحديث البيانات", callback_data=f'manage_{user_id}')],
            [InlineKeyboardButton("⏳ تمديد الاشتراك", callback_data=f'edit_sub_type_{user_id}'), 
             InlineKeyboardButton("🗑️ حذف المشترك", callback_data=f'edit_remove_{user_id}')],
            [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data=list_callback)]
        ]
        
        last_update = now.strftime("%I:%M:%S %p")
        text = (
            f"👤 <b>إدارة المشترك:</b> {sub['first_name']}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"📊 <b>الحالة:</b> {status_text}\n"
            f"📅 <b>ينتهي في:</b> <code>{format_date(sub['subscription_end'])}</code>\n"
            f"⏳ <b>المتبقي:</b> {remaining_str}\n"
            f"🕒 <b>آخر تحديث:</b> {last_update}"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return EDIT_USER_SELECT

    async def edit_sub_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = context.user_data.get('manage_id')
        keyboard = [
            [InlineKeyboardButton("📅 بالأيام", callback_data='edit_type_days'), 
             InlineKeyboardButton("⏱️ بالساعات", callback_data='edit_type_hours')],
            [InlineKeyboardButton("⏳ بالدقائق", callback_data='edit_type_minutes')],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_{user_id}")]
        ]
        await query.edit_message_text("اختر نوع التمديد:", reply_markup=InlineKeyboardMarkup(keyboard))
        return EDIT_SUB_TYPE

    async def edit_action_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data.startswith('edit_sub_type'):
            return await self.edit_sub_type_callback(update, context)
        elif data == 'edit_type_days':
            await query.edit_message_text("أرسل عدد الأيام المراد إضافتها:")
            return EDIT_DAYS
        elif data == 'edit_type_hours':
            await query.edit_message_text("أرسل عدد الساعات المراد إضافتها:")
            return EDIT_HOURS
        elif data == 'edit_type_minutes':
            await query.edit_message_text("أرسل عدد الدقائق المراد إضافتها:")
            return EDIT_MINUTES
        elif data.startswith('edit_remove'):
            user_id = context.user_data.get('manage_id')
            if self.db.remove_subscriber(user_id):
                # طرد المستخدم من القناة عند حذفه يدوياً
                await kick_user_from_channel(self.bot, user_id, self.config.CHANNEL_ID)
                await query.edit_message_text("✅ تم حذف المشترك وطره من القناة.")
            else:
                await query.edit_message_text("❌ حدث خطأ أثناء حذف المشترك.")
            return ConversationHandler.END
        return ConversationHandler.END

    async def get_edit_days(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            days = int(update.message.text)
            user_id = context.user_data['manage_id']
            
            # إلغاء الحظر عند التمديد للسماح بالدخول مجدداً
            await unban_user_from_channel(self.bot, user_id, self.config.CHANNEL_ID)
            
            if self.db.extend_subscription(user_id, days=days):
                sub = self.db.get_subscriber(user_id)
                expiry_str = format_date(sub['subscription_end'])
                await update.message.reply_text(f"✅ تم التمديد بنجاح.\nالتاريخ الجديد: <code>{expiry_str}</code>", parse_mode=ParseMode.HTML)
                
                # إرسال رابط جديد للمشترك
                invite_link = await create_invite_link(self.bot, self.config.CHANNEL_ID, user_id)
                try:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=f"🔄 <b>تم تمديد اشتراكك!</b>\n\n✅ الموعد الجديد للانتهاء: <code>{expiry_str}</code>\n\n🔗 <b>رابط الدخول (صالح لمرة واحدة):</b>\n{invite_link}",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Error sending renewal message to user {user_id}: {e}")
            else:
                await update.message.reply_text("❌ فشل التمديد.")
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ رقم غير صحيح:")
            return EDIT_DAYS

    async def get_edit_hours(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            hours = int(update.message.text)
            user_id = context.user_data['manage_id']
            
            # إلغاء الحظر عند التمديد
            await unban_user_from_channel(self.bot, user_id, self.config.CHANNEL_ID)
            
            if self.db.extend_subscription(user_id, days=0, hours=hours):
                sub = self.db.get_subscriber(user_id)
                expiry_str = format_date(sub['subscription_end'])
                await update.message.reply_text(f"✅ تم التمديد بنجاح.\nالتاريخ الجديد: <code>{expiry_str}</code>", parse_mode=ParseMode.HTML)
                
                # إرسال رابط جديد للمشترك
                invite_link = await create_invite_link(self.bot, self.config.CHANNEL_ID, user_id)
                try:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=f"🔄 <b>تم تمديد اشتراكك!</b>\n\n✅ الموعد الجديد للانتهاء: <code>{expiry_str}</code>\n\n🔗 <b>رابط الدخول (صالح لمرة واحدة):</b>\n{invite_link}",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Error sending renewal message to user {user_id}: {e}")
            else:
                await update.message.reply_text("❌ فشل التمديد.")
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ رقم غير صحيح:")
            return EDIT_HOURS

    async def get_edit_minutes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            minutes = int(update.message.text)
            user_id = context.user_data['manage_id']
            
            # إلغاء الحظر عند التمديد
            await unban_user_from_channel(self.bot, user_id, self.config.CHANNEL_ID)
            
            if self.db.extend_subscription(user_id, days=0, hours=0, minutes=minutes):
                sub = self.db.get_subscriber(user_id)
                expiry_str = format_date(sub['subscription_end'])
                await update.message.reply_text(f"✅ تم التمديد بنجاح.\nالتاريخ الجديد: <code>{expiry_str}</code>", parse_mode=ParseMode.HTML)
                
                # إرسال رابط جديد للمشترك
                invite_link = await create_invite_link(self.bot, self.config.CHANNEL_ID, user_id)
                try:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=f"🔄 <b>تم تمديد اشتراكك!</b>\n\n✅ الموعد الجديد للانتهاء: <code>{expiry_str}</code>\n\n🔗 <b>رابط الدخول (صالح لمرة واحدة):</b>\n{invite_link}",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Error sending renewal message to user {user_id}: {e}")
            else:
                await update.message.reply_text("❌ فشل التمديد.")
            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("❌ رقم غير صحيح:")
            return EDIT_MINUTES

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("تم إلغاء المحادثة.")
        return ConversationHandler.END

    async def handle_callback_general(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data == 'force_check':
            from scheduler import NotificationScheduler
            s = NotificationScheduler(self.bot, self.db)
            await s.check_expired_subscriptions()
            await s.check_notifications()
            await query.message.reply_text("✅ تم الانتهاء من الفحص وتحديث الحالات.")
        elif data == 'help':
            help_text = (
                "❓ <b>تعليمات الاستخدام:</b>\n\n"
                "1️⃣ لإضافة مشترك: اضغط على 'إضافة مشترك' وأرسل الآيدي الخاص به.\n"
                "2️⃣ لإدارة المشتركين: اضغط على 'قائمة المشتركين' واختر المستخدم.\n"
                "3️⃣ عند انتهاء الاشتراك: سيتم طرد المستخدم وحظره تلقائياً.\n"
                "4️⃣ للتجديد: ابحث عن المستخدم في القائمة واختر 'تمديد الاشتراك'."
            )
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data='back_to_start')]]
            await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        elif data == 'back_to_start':
            user_id = update.effective_user.id
            if await self.is_admin(user_id):
                keyboard = [
                    [InlineKeyboardButton("➕ إضافة مشترك", callback_data='add_user_flow')],
                    [InlineKeyboardButton("📋 قائمة المشتركين", callback_data='list_users_flow'), 
                     InlineKeyboardButton("🔴 المنتهيين", callback_data='list_expired_flow')],
                    [InlineKeyboardButton("🔍 فحص سريع", callback_data='force_check'), InlineKeyboardButton("❓ مساعدة", callback_data='help')]
                ]
                await query.edit_message_text(self.config.MESSAGES['admin_welcome'], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                keyboard = [[InlineKeyboardButton("📊 فحص اشتراكي", callback_data='check_my_sub')]]
                await query.edit_message_text(self.config.MESSAGES['welcome'], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    async def chat_join_request_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة طلبات الانضمام للقناة بناءً على حالة الاشتراك"""
        request = update.chat_join_request
        user_id = request.from_user.id
        
        # البحث عن المشترك في قاعدة البيانات
        sub = self.db.get_subscriber(user_id)
        
        # توقيت السعودية الفعلي للمقارنة
        import pytz
        tz = pytz.timezone("Asia/Riyadh")
        now = datetime.now(tz)

        if sub and sub['status'] == 'active':
            expiry = sub['subscription_end']
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            
            if expiry.tzinfo is None:
                expiry = tz.localize(expiry)
            else:
                expiry = expiry.astimezone(tz)
            
            if expiry > now:
                # اشتراك فعال وساري - قبول الطلب
                await request.approve()
                logger.info(f"✅ Approved join request for active subscriber {user_id}")
                return
            else:
                # اشتراك موجود ولكنه منتهي - رفض وحظر
                await request.decline()
                await kick_user_from_channel(self.bot, user_id, self.config.CHANNEL_ID)
                logger.info(f"❌ Declined and banned expired user {user_id}")
                try:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text="❌ عفواً، اشتراكك منتهي. يرجى التجديد للحصول على صلاحية الدخول.",
                        parse_mode=ParseMode.HTML
                    )
                except: pass
                return
        
        # إذا لم يكن مشتركاً أصلاً - رفض وحظر فوري
        await request.decline()
        await kick_user_from_channel(self.bot, user_id, self.config.CHANNEL_ID)
        logger.info(f"🚫 Unauthorized join request: Banned user {user_id}")
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text="⚠️ لا يوجد لديك اشتراك فعال. تم حظرك من القناة. تواصل مع الإدارة للاشتراك.",
                parse_mode=ParseMode.HTML
            )
        except: pass

    def register_handlers(self, application: Application):
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CallbackQueryHandler(self.check_my_sub_callback, pattern='^check_my_sub$'))
        application.add_handler(ChatJoinRequestHandler(self.chat_join_request_handler))
        
        # Add ConversationHandler for adding users
        add_user_conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.add_user_start, pattern='^add_user_flow$'),
            ],
            states={
                GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_user_id)],
                CONFIRM_USER: [CallbackQueryHandler(self.confirm_user_callback)],
                GET_SUB_TYPE: [CallbackQueryHandler(self.get_sub_type_callback)],
                GET_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_subscription_days)],
                GET_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_subscription_hours)],
                GET_MINUTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_subscription_minutes)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_chat=True,
            name="add_user"
        )
        
        # Add ConversationHandler for editing users
        edit_user_conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.list_users_flow, pattern='^list_(users|expired)_flow$'),
            ],
            states={
                EDIT_USER_SELECT: [
                    CallbackQueryHandler(self.manage_user_callback, pattern='^manage_'),
                    CallbackQueryHandler(self.edit_action_callback, pattern='^edit_'),
                    CallbackQueryHandler(self.list_users_flow, pattern='^list_(users|expired)_flow$'),
                ],
                EDIT_SUB_TYPE: [
                    CallbackQueryHandler(self.edit_action_callback, pattern='^edit_type_'),
                    CallbackQueryHandler(self.manage_user_callback, pattern='^manage_'),
                ],
                EDIT_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_edit_days)],
                EDIT_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_edit_hours)],
                EDIT_MINUTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_edit_minutes)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_chat=True,
            name="edit_user"
        )
        
        application.add_handler(add_user_conv)
        application.add_handler(edit_user_conv)
        
        # Add general callback handlers
        application.add_handler(CallbackQueryHandler(self.handle_callback_general, pattern='^(force_check|help|back_to_start)$'))
        application.add_handler(CallbackQueryHandler(self.manage_user_callback, pattern='^manage_')) # Fallback for manage outside conv
