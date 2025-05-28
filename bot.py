import logging
import sqlite3
import speech_recognition as sr
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# تنظیمات پیشرفته لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DatabaseManager:
    """کلاس مدیریت پایگاه داده با قابلیت اتصال ایمن"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """ایجاد جداول مورد نیاز"""
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    time TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_user_time 
                ON reminders(user_id, time)
            """)
    
    def get_connection(self):
        """ایجاد اتصال جدید به پایگاه داده"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

class VoiceRecognizer:
    """کلاس پردازش صوت با قابلیت تشخیص گفتار"""
    
    def __init__(self):
        self.recognizer = sr.Recognizer()
    
    async def recognize_voice(self, voice_file):
        """تبدیل صوت به متن"""
        try:
            voice_path = "temp_voice.ogg"
            await voice_file.download_to_drive(voice_path)
            
            with sr.AudioFile(voice_path) as source:
                audio = self.recognizer.record(source)
                text = self.recognizer.recognize_google(audio, language="fa-IR")
            
            os.remove(voice_path)  # حذف فایل موقت
            return text
        except Exception as e:
            logger.error(f"Voice recognition error: {e}")
            raise

class ReminderBot:
    """کلاس اصلی ربات یادآور"""
    
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        if not self.token:
            raise ValueError("❌ توکن تلگرام تنظیم نشده است!")
        
        self.db = DatabaseManager('/opt/render/project/src/reminders.db')
        self.voice_recognizer = VoiceRecognizer()
        self.app = self._setup_application()
    
    def _setup_application(self):
        """تنظیمات اولیه اپلیکیشن"""
        app = (
            ApplicationBuilder()
            .token(self.token)
            .concurrent_updates(True)
            .build()
        )
        
        # ثبت هندلرها
        handlers = [
            CommandHandler("start", self.start),
            MessageHandler(filters.VOICE, self.handle_voice),
            CallbackQueryHandler(self.handle_confirmation),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.save_reminder)
        ]
        
        for handler in handlers:
            app.add_handler(handler)
        
        # تنظیم JobQueue برای بررسی یادآوری‌ها
        app.job_queue.run_repeating(
            self.check_reminders,
            interval=60.0,
            first=10.0
        )
        
        return app
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """دستور شروع ربات"""
        welcome_msg = (
            "سلام عزیزم! 👋\n"
            "من ربات یادآور هوشمند شما هستم.\n"
            "میتونی یکی از این کارها رو انجام بدی:\n"
            "- ویس بفرستی تا برات تبدیل به متن کنم\n"
            "- یا مستقیماً متن و زمان یادآوری رو برام بفرستی"
        )
        await update.message.reply_text(welcome_msg)
    
    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پردازش پیام‌های صوتی"""
        try:
            voice_file = await update.message.voice.get_file()
            text = await self.voice_recognizer.recognize_voice(voice_file)
            
            keyboard = [
                [InlineKeyboardButton("✅ بله تایید است", callback_data=f"confirm_{text}")],
                [InlineKeyboardButton("✏️ ویرایش متن", callback_data="edit")]
            ]
            
            await update.message.reply_text(
                f"🔊 متن شناسایی شده:\n{text}\n\nآیا درست است؟",
                reply_markup=InlineKeyboardMarkup(keyboard)
                
        except sr.UnknownValueError:
            await update.message.reply_text("متوجه صحبت‌های شما نشدم! لطفاً دوباره امتحان کنید.")
        except Exception as e:
            logger.error(f"Voice handling error: {e}")
            await update.message.reply_text("خطا در پردازش صدا! لطفاً بعداً تلاش کنید.")
    
    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پردازش تأیید متن"""
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith("confirm_"):
            text = query.data[8:]
            context.user_data['reminder_text'] = text
            await query.edit_message_text(
                "⏰ لطفاً زمان یادآوری را به فرمت زیر وارد کنید:\n"
                "مثال: 1403-05-15 14:30"
            )
        else:
            context.user_data['awaiting_edit'] = True
            await query.edit_message_text("لطفاً متن جدید را ارسال کنید:")
    
    async def save_reminder(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ذخیره یادآوری جدید"""
        try:
            user_id = update.message.from_user.id
            text = context.user_data.get('reminder_text', '')
            time_str = update.message.text
            
            # بررسی حالت ویرایش
            if context.user_data.get('awaiting_edit'):
                context.user_data['reminder_text'] = update.message.text
                context.user_data['awaiting_edit'] = False
                await update.message.reply_text(
                    "متن با موفقیت به‌روز شد.\n"
                    "⏰ لطفاً زمان یادآوری را وارد کنید:"
                )
                return
            
            # اعتبارسنجی زمان
            try:
                reminder_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                if reminder_time < datetime.now():
                    raise ValueError("زمان گذشته")
            except ValueError:
                await update.message.reply_text(
                    "⚠️ فرمت زمان نامعتبر است!\n"
                    "لطفاً به فرمت YYYY-MM-DD HH:MM وارد کنید.\n"
                    "مثال: 1403-05-15 14:30"
                )
                return
            
            # ذخیره در دیتابیس
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO reminders (user_id, text, time) VALUES (?, ?, ?)",
                    (user_id, text, time_str)
                )
            
            await update.message.reply_text(
                "✅ یادآوری با موفقیت ثبت شد!\n"
                f"📝 متن: {text}\n"
                f"⏰ زمان: {time_str}"
            )
            
            # پاکسازی داده‌های موقت
            context.user_data.pop('reminder_text', None)
            
        except Exception as e:
            logger.error(f"Error saving reminder: {e}")
            await update.message.reply_text(
                "⚠️ خطا در ثبت یادآوری!\n"
                "لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید."
            )
    
    async def check_reminders(self, context: ContextTypes.DEFAULT_TYPE):
        """بررسی و ارسال یادآوری‌های زمان‌رسیده"""
        try:
            with self.db.get_connection() as conn:
                reminders = conn.execute("""
                    SELECT id, user_id, text 
                    FROM reminders 
                    WHERE datetime(time) <= datetime('now', 'localtime')
                """).fetchall()
                
                for reminder in reminders:
                    try:
                        await context.bot.send_message(
                            reminder['user_id'],
                            f"🔔 یادآوری:\n{reminder['text']}"
                        )
                        conn.execute(
                            "DELETE FROM reminders WHERE id = ?",
                            (reminder['id'],)
                        )
                    except Exception as e:
                        logger.error(f"Error sending reminder: {e}")
                        # اگر کاربر ربات را بلاک کرده باشد
                        if "blocked" in str(e).lower():
                            conn.execute(
                                "DELETE FROM reminders WHERE id = ?",
                                (reminder['id'],)
                            )
        except Exception as e:
            logger.error(f"Error in reminder job: {e}")

def main():
    """تابع اصلی اجرای ربات"""
    try:
        bot = ReminderBot()
        logger.info("ربات در حال اجرا است...")
        bot.app.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.critical(f"ربات متوقف شد: {e}")
        raise

if __name__ == "__main__":
    main()
