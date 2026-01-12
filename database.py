import psycopg2
from psycopg2.extras import RealDictCursor
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import pytz
import os

logger = logging.getLogger(__name__)

def fix_database_url(db_url: str) -> str:
    """
    إصلاح رابط قاعدة البيانات من postgres:// إلى postgresql://
    """
    if db_url and db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    return db_url


class Database:
    def __init__(self, db_url: str = None, timezone: str = "Asia/Riyadh"):
        """
        تهيئة اتصال قاعدة البيانات
        """
        # الحصول على رابط قاعدة البيانات
        self.db_url = db_url or os.getenv("DATABASE_URL")

        if not self.db_url:
            logger.error("❌ DATABASE_URL is not set!")
            raise ValueError("DATABASE_URL is required")

        # إصلاح رابط قاعدة البيانات
        self.db_url = fix_database_url(self.db_url)

        # تعيين المنطقة الزمنية
        self.timezone = pytz.timezone(timezone)

        # تهيئة قاعدة البيانات
        try:
            # التحقق مما إذا كنا في بيئة Replit المحلية
            self.is_replit = os.getenv("REPLIT_ENVIRONMENT") == "true" or "REPLIT_DB_URL" in os.environ
            self.init_database()
            logger.info("✅ Database initialized successfully")
        except Exception as e:
            logger.error(f"❌ Database initialization failed: {e}")
            raise

    def get_connection(self):
        """
        إنشاء اتصال بقاعدة البيانات
        """
        try:
            # في بيئة ريبليت المحلية، قد لا تدعم قاعدة البيانات SSL
            # نستخدم 'prefer' بدلاً من 'require' للسماح بالاتصال بدون SSL إذا لم يكن مدعوماً
            ssl_mode = 'prefer' if getattr(self, 'is_replit', False) else 'require'
            
            # Forcing sslmode to prefer for standard PostgreSQL if require fails
            conn = psycopg2.connect(
                self.db_url,
                cursor_factory=RealDictCursor,
                sslmode=ssl_mode,
                connect_timeout=10
            )
            return conn
        except Exception as e:
            logger.error(f"❌ Database connection error: {e}")
            raise

    def init_database(self):
        """
        تهيئة قاعدة البيانات والجداول
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # جدول المشتركين
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS subscribers (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT UNIQUE NOT NULL,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        subscription_start TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        subscription_end TIMESTAMP WITH TIME ZONE NOT NULL,
                        status TEXT DEFAULT 'active',
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # جدول الإشعارات
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS notifications (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        notification_type TEXT,
                        notification_date TIMESTAMP WITH TIME ZONE,
                        sent BOOLEAN DEFAULT FALSE,
                        sent_at TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES subscribers (user_id) ON DELETE CASCADE
                    )
                ''')

                # جدول السجلات
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS logs (
                        id SERIAL PRIMARY KEY,
                        action TEXT,
                        user_id BIGINT,
                        admin_id BIGINT,
                        details TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # إنشاء الفهارس
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_subscribers_user_id ON subscribers(user_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_subscribers_status ON subscribers(status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_sent ON notifications(sent)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id)')

            conn.commit()

    def add_subscriber(self, user_id: int, username: str, first_name: str,
                       last_name: str, days: int = 0, hours: int = 0, minutes: int = 0) -> bool:
        """إضافة مشترك جديد"""
        try:
            # التأكد من استخدام توقيت السعودية الفعلي
            tz = pytz.timezone("Asia/Riyadh")
            now = datetime.now(tz)
            subscription_end = now + timedelta(days=days, hours=hours, minutes=minutes)

            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO subscribers 
                        (user_id, username, first_name, last_name, 
                         subscription_start, subscription_end, status, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        subscription_start = EXCLUDED.subscription_start,
                        subscription_end = EXCLUDED.subscription_end,
                        status = 'active',
                        updated_at = EXCLUDED.updated_at
                    ''', (user_id, username, first_name, last_name,
                          now, subscription_end, now))

                    # تسجيل العملية
                    details = f"Added for {days} days, {hours} hours, and {minutes} minutes"
                    cursor.execute('''
                        INSERT INTO logs (action, user_id, details)
                        VALUES ('add', %s, %s)
                    ''', (user_id, details))

                    # حذف الإشعارات القديمة إن وجدت
                    cursor.execute('DELETE FROM notifications WHERE user_id = %s', (user_id,))

                    # إضافة الإشعارات
                    total_minutes = days * 1440 + hours * 60 + minutes

                    intervals = []
                    type_prefix = "minutes"
                    if total_minutes >= 1440:
                        intervals = [7, 3, 1, 0]
                        type_prefix = "days"
                    elif total_minutes >= 60:
                        intervals = [60, 30, 10, 0]
                        type_prefix = "minutes"
                    else:
                        intervals = [10, 5, 2, 1, 0]
                        type_prefix = "minutes"

                    cursor.execute('''
                        INSERT INTO notifications (user_id, notification_type, notification_date)
                        VALUES (%s, %s, %s)
                    ''', (user_id, "5_seconds", (subscription_end - timedelta(seconds=5))))

                    for val in intervals:
                        if type_prefix == "days":
                            notif_date = subscription_end - timedelta(days=val)
                        else:
                            notif_date = subscription_end - timedelta(minutes=val)

                        if notif_date > now:
                            cursor.execute('''
                                INSERT INTO notifications (user_id, notification_type, notification_date)
                                VALUES (%s, %s, %s)
                            ''', (user_id, f"{val}_{type_prefix}", notif_date))

                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding subscriber: {e}")
            return False

    def extend_subscription(self, user_id: int, days: int = 0, hours: int = 0, minutes: int = 0) -> bool:
        """تمديد اشتراك مستخدم"""
        try:
            now = datetime.now(self.timezone)
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute('SELECT subscription_start, subscription_end FROM subscribers WHERE user_id = %s', (user_id,))
                    result = cursor.fetchone()

                    if result:
                        current_start = result['subscription_start']
                        current_end = result['subscription_end']
                        new_end = current_end + timedelta(days=days, hours=hours, minutes=minutes)

                        cursor.execute('''
                            UPDATE subscribers 
                            SET subscription_end = %s, status = 'active', updated_at = %s
                            WHERE user_id = %s
                        ''', (new_end, now, user_id))

                        details = f"Extended by {days} days, {hours} hours, and {minutes} minutes"
                        cursor.execute('''
                            INSERT INTO logs (action, user_id, details)
                            VALUES ('extend', %s, %s)
                        ''', (user_id, details))

                        cursor.execute('DELETE FROM notifications WHERE user_id = %s AND sent = FALSE', (user_id,))

                        total_duration_delta = new_end - current_start
                        total_minutes = total_duration_delta.total_seconds() / 60

                        intervals = []
                        type_prefix = "minutes"
                        if total_minutes >= 1440:
                            intervals = [7, 3, 1, 0]
                            type_prefix = "days"
                        elif total_minutes >= 60:
                            intervals = [60, 30, 10, 0]
                            type_prefix = "minutes"
                        else:
                            intervals = [10, 5, 2, 1, 0]
                            type_prefix = "minutes"

                        cursor.execute('''
                            INSERT INTO notifications (user_id, notification_type, notification_date)
                            VALUES (%s, %s, %s)
                        ''', (user_id, "5_seconds", (new_end - timedelta(seconds=5))))

                        for val in intervals:
                            if type_prefix == "days":
                                notif_date = new_end - timedelta(days=val)
                            else:
                                notif_date = new_end - timedelta(minutes=val)

                            if notif_date > now:
                                cursor.execute('''
                                    INSERT INTO notifications (user_id, notification_type, notification_date)
                                    VALUES (%s, %s, %s)
                                ''', (user_id, f"{val}_{type_prefix}", notif_date))

                        conn.commit()
                        return True
                    return False
        except Exception as e:
            logger.error(f"Error extending subscription: {e}")
            return False

    def get_subscriber(self, user_id: int) -> Optional[Dict]:
        """الحصول على بيانات مشترك"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute('SELECT * FROM subscribers WHERE user_id = %s', (user_id,))
                    result = cursor.fetchone()
                    return dict(result) if result else None
        except Exception as e:
            logger.error(f"Error getting subscriber: {e}")
            return None

    def get_all_subscribers(self, status: str = None) -> List[Dict]:
        """الحصول على جميع المشتركين"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    if status:
                        cursor.execute('SELECT * FROM subscribers WHERE status = %s ORDER BY subscription_end', (status,))
                    else:
                        cursor.execute('SELECT * FROM subscribers ORDER BY subscription_end')
                    return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting all subscribers: {e}")
            return []

    def update_subscriber_status(self, user_id: int, status: str) -> bool:
        """تحديث حالة المشترك"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        UPDATE subscribers 
                        SET status = %s, updated_at = %s
                        WHERE user_id = %s
                    ''', (status, datetime.now(self.timezone), user_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error updating subscriber status: {e}")
            return False

    def remove_subscriber(self, user_id: int) -> bool:
        """حذف مشترك من قاعدة البيانات"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('DELETE FROM notifications WHERE user_id = %s', (user_id,))
                    cursor.execute('DELETE FROM subscribers WHERE user_id = %s', (user_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error removing subscriber: {e}")
            return False

    def mark_notification_sent(self, notification_id: int):
        """تحديد الإشعار كمرسل"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        UPDATE notifications 
                        SET sent = TRUE, sent_at = %s
                        WHERE id = %s
                    ''', (datetime.now(self.timezone), notification_id))
                conn.commit()
        except Exception as e:
            logger.error(f"Error marking notification as sent: {e}")

    def get_pending_notifications(self) -> List[Dict]:
        """الحصول على الإشعارات المعلقة"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute('''
                        SELECT n.*, s.username, s.first_name 
                        FROM notifications n
                        JOIN subscribers s ON n.user_id = s.user_id
                        WHERE n.sent = FALSE AND n.notification_date <= %s
                    ''', (datetime.now(self.timezone),))
                    return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting pending notifications: {e}")
            return []