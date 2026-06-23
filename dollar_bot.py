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
from telegram import Update, MessageEntity
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
ALANCHAND_TOKEN = os.environ.get("ALANCHAND_TOKEN", "")

ALANCHAND_URL = f"https://api.alanchand.com/?type=currencies&token={ALANCHAND_TOKEN}"
ALANCHAND_GOLD_URL = f"https://api.alanchand.com/?type=golds&token={ALANCHAND_TOKEN}"
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


def _extract_list_of_items(data):
    """
    تلاش می‌کند لیست آیتم‌ها (مثلاً انواع طلا) را از ساختار JSON پیدا کند،
    چه خودِ پاسخ یک لیست باشد چه داخل یک کلید مثل 'golds' یا 'data' باشد.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("golds", "gold", "data", "items", "result"):
            if key in data and isinstance(data[key], list):
                return data[key]
        # اگر هیچ‌کدام نبود، شاید مقادیر دیکشنری خودشان آیتم‌ها باشند
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


def fetch_gold_prices() -> str:
    if not ALANCHAND_TOKEN:
        raise RuntimeError("ALANCHAND_TOKEN ست نشده")

    resp = requests.get(ALANCHAND_GOLD_URL, headers=REQUEST_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    items = _extract_list_of_items(data)

    if not items:
        raise RuntimeError("لیست قیمت طلا در پاسخ پیدا نشد")

    price_keys = ("price", "value", "sell", "rate", "amount", "Price")
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("name_fa") or item.get("name_en") or item.get("symbol") or "نامشخص"
        price = None
        for pk in price_keys:
            for k in item:
                if str(k).lower() == pk.lower():
                    try:
                        price = float(item[k])
                    except (TypeError, ValueError):
                        pass
                    break
            if price is not None:
                break
        if price is not None:
            lines.append(f"🔸 {name}: {_format_price(price)}")

    if not lines:
        raise RuntimeError("هیچ قیمتی از پاسخ استخراج نشد")

    return "🏆 نرخ انواع طلا:\n\n" + "\n".join(lines)


def fetch_usd_price() -> str:
    errors = []

    # منبع ۱: AlanChand (دقیق‌تره، ولی نیاز به توکن رایگان دارد)
    if ALANCHAND_TOKEN:
        try:
            resp = requests.get(ALANCHAND_URL, headers=REQUEST_HEADERS, timeout=10)
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


def build_full_report() -> str:
    parts = []
    try:
        parts.append(fetch_gold_prices())
    except Exception:
        logger.exception("خطا در دریافت قیمت طلا (گزارش کامل)")

    try:
        usd = fetch_usd_price()
        parts.append(f"💵 قیمت دلار = {usd}")
    except Exception:
        logger.exception("خطا در دریافت قیمت دلار (گزارش کامل)")

    if not parts:
        return "متاسفانه الان نتونستم قیمت‌ها رو بگیرم. کمی بعد دوباره تلاش کن."

    return "\n\n".join(parts)


async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_full_report())


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_usd_price()
        await update.message.reply_text(f"💵 قیمت دلار آزاد:\n{price}")
    except Exception:
        logger.exception("خطا در دریافت قیمت")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def gold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = fetch_gold_prices()
        await update.message.reply_text(text)
    except Exception:
        logger.exception("خطا در دریافت قیمت طلا")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت طلا رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! برای دیدن قیمت لحظه‌ای دلار آزاد دستور /price رو بفرست،\n"
        "برای نرخ طلا دستور /gold رو بفرست،\n"
        "یا فقط کلمه «دلار» یا «طلا» رو برام بنویس."
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = message.text or ""

    # بررسی اینکه آیا بات در همین پیام منشن (@username) شده یا نه
    bot_username = context.bot.username
    if message.entities and bot_username:
        for ent in message.entities:
            if ent.type == MessageEntity.MENTION:
                mention_text = text[ent.offset: ent.offset + ent.length]
                if mention_text.lower() == f"@{bot_username}".lower():
                    await mention_handler(update, context)
                    return

    if "طلا" in text:
        await gold_command(update, context)
    elif "دلار" in text:
        await price_command(update, context)


def main():
    if BOT_TOKEN == "PUT-YOUR-TOKEN-HERE":
        raise SystemExit("لطفاً اول BOT_TOKEN رو با توکن واقعی بات خودت جایگزین کن.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("gold", gold_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("بات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
