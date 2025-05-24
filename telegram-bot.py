import logging
import sqlite3
import speech_recognition as sr
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# دریافت توکن از متغیر محیطی
telegram_token = os.getenv("TELEGRAM_TOKEN")
if not telegram_token:
    raise ValueError("❌ توکن تلگرام تنظیم نشده است!")

# ساخت اپلیکیشن با قابلیت JobQueue
app = (
    ApplicationBuilder()
    .token(telegram_token)
    .concurrent_updates(True)
    .build()
)

# مدیریت دیتابیس
def get_db_connection():
    conn = sqlite3.connect('/opt/render/project/src/reminders.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                text TEXT,
                time TEXT
            )
        """)

# دستورات بات
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من یادیار هستم. پیام صوتی یا متنی بفرست تا یادآوری ثبت کنم.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        voice_file = await update.message.voice.get_file()
        voice_path = await voice_file.download_to_drive()
        
        recognizer = sr.Recognizer()
        with sr.AudioFile(voice_path) as source:
            audio = recognizer.record(source)
            text = recognizer.recognize_google(audio, language="fa-IR")
        
        keyboard = [
            [InlineKeyboardButton("✅ بله تایید است", callback_data=f"confirm_{text}")],
            [InlineKeyboardButton("✏️ ویرایش متن", callback_data="edit")]
        ]
        
        await update.message.reply_text(
            f"متن شناسایی شده:\n{text}\n\nآیا درست است؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.error(f"Error in voice handling: {e}")
        await update.message.reply_text("خطا در پردازش صدا! لطفاً دوباره امتحان کنید.")

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("confirm_"):
        text = query.data[8:]  # حذف پیشوند confirm_
        context.user_data['reminder_text'] = text
        await query.edit_message_text("⏰ لطفاً زمان یادآوری را به فرمت زیر وارد کنید:\nYYYY-MM-DD HH:MM")
    else:
        await query.edit_message_text("لطفاً متن جدید را ارسال کنید:")

async def save_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        text = context.user_data.get('reminder_text', '')
        time = update.message.text
        
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO reminders (user_id, text, time) VALUES (?, ?, ?)",
                (user_id, text, time)
            )
        
        await update.message.reply_text(
            f"✅ یادآوری ثبت شد:\n"
            f"📝 متن: {text}\n"
            f"⏰ زمان: {time}"
        )
    except Exception as e:
        logging.error(f"Error saving reminder: {e}")
        await update.message.reply_text("خطا در ثبت یادآوری! فرمت زمان را چک کنید.")

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db_connection() as conn:
            reminders = conn.execute(
                "SELECT id, user_id, text FROM reminders WHERE time <= datetime('now')"
            ).fetchall()
            
            for reminder in reminders:
                try:
                    await context.bot.send_message(
                        reminder['user_id'],
                        f"🔔 یادآوری:\n{reminder['text']}"
                    )
                    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder['id'],))
                except Exception as e:
                    logging.error(f"Error sending reminder: {e}")
    except Exception as e:
        logging.error(f"Error in reminder job: {e}")

# تنظیمات اصلی
def main():
    init_db()
    
    # ثبت هندلرها
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_confirmation))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_reminder))
    
    # تنظیم JobQueue
    job_queue = app.job_queue
    job_queue.run_repeating(check_reminders, interval=60.0, first=10.0)
    
    # اجرای بات
    app.run_polling()

if __name__ == "__main__":
    main()