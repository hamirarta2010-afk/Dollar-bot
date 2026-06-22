"""
ربات تلگرام نمایش قیمت لحظه‌ای دلار آزاد (بازار ایران)
----------------------------------------------------
پیش‌نیازها:
    pip install python-telegram-bot==21.* requests

تنظیمات:
    1. از @BotFather در تلگرام یک بات بساز و توکن بگیر.
    2. توکن رو در متغیر BOT_TOKEN پایین قرار بده (یا به‌صورت متغیر محیطی ست کن).
    3. اجرا کن: python dollar_bot.py

منبع قیمت:
    از وب‌سرویس رایگان brsapi.ir استفاده شده (بدون نیاز به کلید/ثبت‌نام).
    اگر این سرویس از کار افتاد یا فرمت پاسخش تغییر کرد، فقط کافیه آدرس
    API_URL و تابع extract_usd_price رو با منبع جدید (مثلاً alanchand.com
    یا navasan.tech) هماهنگ کنی.
"""

import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
API_URL = "https://brsapi.ir/FreeTsetmcBourseApi/Api_Free_Gold_Currency_v2.json"


def extract_usd_price(data: dict) -> str:
    """
    سعی می‌کند قیمت دلار را از ساختار JSON برگشتی پیدا کند.
    چون فرمت دقیق این APIهای رایگان گاهی تغییر می‌کند، چند حالت رایج
    را پوشش می‌دهیم.
    """
    candidates = []
    if isinstance(data, dict):
        for key in ("currency", "currencies", "ارز", "Currency"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
    elif isinstance(data, list):
        candidates = data

    for item in candidates:
        if not isinstance(item, dict):
            continue
        name_en = str(item.get("name_en") or item.get("symbol") or "").upper()
        name_fa = str(item.get("name") or "")
        if "USD" in name_en or "دلار" in name_fa:
            price = item.get("price") or item.get("Price") or item.get("value")
            if price:
                return str(price)

    raise ValueError("قیمت دلار در پاسخ سرویس پیدا نشد")


def fetch_usd_price() -> str:
    resp = requests.get(API_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    price_str = extract_usd_price(data)
    price_int = int(float(price_str))
    return f"{price_int:,} تومان".replace(",", "،")


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_usd_price()
        await update.message.reply_text(f"💵 قیمت دلار آزاد:\n{price}")
    except Exception as e:
        logger.exception("خطا در دریافت قیمت")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! برای دیدن قیمت لحظه‌ای دلار آزاد دستور /price رو بفرست،\n"
        "یا فقط کلمه «دلار» رو برام بنویس."
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "دلار" in (update.message.text or ""):
        await price_command(update, context)


def main():
    if BOT_TOKEN == "PUT-YOUR-TOKEN-HERE":
        raise SystemExit("لطفاً اول BOT_TOKEN رو با توکن واقعی بات خودت جایگزین کن.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("بات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
