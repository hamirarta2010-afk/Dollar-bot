"""
ربات تلگرام نمایش قیمت لحظه‌ای دلار آزاد (بازار ایران)
----------------------------------------------------
پیش‌نیازها:
    pip install python-telegram-bot==21.* requests

تنظیمات:
    1. از @BotFather در تلگرام یک بات بساز و توکن بگیر -> متغیر BOT_TOKEN
    2. از @alanchand_token_bot در تلگرام یک توکن رایگان بگیر -> متغیر ALANCHAND_TOKEN
       (فقط /start رو به این بات بزن، خودش توکن می‌ده. کاملاً رایگانه.)
    3. هر دو رو به‌عنوان متغیر محیطی (Environment Variable) ست کن.
    4. اجرا کن: python dollar_bot.py

منابع قیمت:
    اول از AlanChand (نیاز به توکن رایگان) استفاده می‌شه؛ اگه جواب نداد،
    به‌صورت خودکار به priceto.day (بدون نیاز به توکن) سوییچ می‌کنه.
"""

import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
ALANCHAND_TOKEN = os.environ.get("ALANCHAND_TOKEN", "")

ALANCHAND_URL = "https://api.alanchand.com?type=currency&symbols=usd"
PRICETODAY_URL = "https://api.priceto.day/v1/latest/irr/usd"

# بعضی سرویس‌ها درخواست‌های بدون User-Agent مرورگر را به‌عنوان بات رد می‌کنند
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _try_parse_plain_number(text: str):
    """اگر پاسخ یک عدد ساده (متن یا JSON عددی) بود، آن را برمی‌گرداند."""
    text = text.strip().strip('"')
    try:
        return float(text)
    except ValueError:
        return None


def _deep_find_usd_price(data):
    """
    به‌صورت بازگشتی در هر ساختار JSON دنبال قیمت دلار می‌گردد.
    چون فرمت دقیق APIهای مختلف فرق دارد، این تابع چند الگوی رایج را پوشش می‌دهد.
    """
    price_keys = ("price", "value", "sell", "rate", "amount", "Price")

    if isinstance(data, dict):
        keys_lower = {str(k).lower(): k for k in data.keys()}

        # حالت ۱: کلیدی دقیقاً به اسم usd وجود دارد
        if "usd" in keys_lower:
            usd_val = data[keys_lower["usd"]]
            if isinstance(usd_val, dict):
                for pk in price_keys:
                    for k2 in usd_val:
                        if str(k2).lower() == pk.lower():
                            try:
                                return float(usd_val[k2])
                            except (TypeError, ValueError):
                                pass
            else:
                try:
                    return float(usd_val)
                except (TypeError, ValueError):
                    pass

        # حالت ۲: این دیکشنری خودش یک آیتم با symbol/name_en برابر usd است
        symbol = str(
            data.get("symbol") or data.get("Symbol") or data.get("name_en") or ""
        ).lower()
        if symbol == "usd":
            for pk in price_keys:
                for k2 in data:
                    if str(k2).lower() == pk.lower():
                        try:
                            return float(data[k2])
                        except (TypeError, ValueError):
                            pass

        # در غیر این صورت، بازگشتی در مقادیر دیگر بگرد
        for v in data.values():
            result = _deep_find_usd_price(v)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _deep_find_usd_price(item)
            if result is not None:
                return result

    return None


def _format_price(number: float) -> str:
    return f"{int(number):,} تومان".replace(",", "،")


def fetch_usd_price() -> str:
    errors = []

    # منبع ۱: AlanChand (دقیق‌تره، ولی نیاز به توکن رایگان دارد)
    if ALANCHAND_TOKEN:
        try:
            headers = dict(REQUEST_HEADERS)
            headers["Authorization"] = f"Bearer {ALANCHAND_TOKEN}"
            resp = requests.get(ALANCHAND_URL, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            price = _deep_find_usd_price(data)
            if price:
                return _format_price(price)
            errors.append("alanchand: قیمت در پاسخ پیدا نشد")
        except Exception as e:
            errors.append(f"alanchand: {e}")
    else:
        errors.append("alanchand: ALANCHAND_TOKEN ست نشده")

    # منبع ۲ (پشتیبان): priceto.day (بدون نیاز به توکن)
    try:
        resp = requests.get(PRICETODAY_URL, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        number = _try_parse_plain_number(resp.text)
        if number is None:
            data = resp.json()
            if isinstance(data, (int, float)):
                number = float(data)
            else:
                number = _deep_find_usd_price(data)
        if number:
            return _format_price(number)
        errors.append("priceto.day: قیمت در پاسخ پیدا نشد")
    except Exception as e:
        errors.append(f"priceto.day: {e}")

    raise RuntimeError(" | ".join(errors))


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_usd_price()
        await update.message.reply_text(f"💵 قیمت دلار آزاد:\n{price}")
    except Exception:
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
