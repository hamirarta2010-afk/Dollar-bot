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
import asyncio
import logging
import requests
from telegram import (
    Update,
    MessageEntity,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
ALANCHAND_TOKEN = os.environ.get("ALANCHAND_TOKEN", "")

ALANCHAND_URL = f"https://api.alanchand.com/?type=currencies&token={ALANCHAND_TOKEN}"
ALANCHAND_GOLD_URL = f"https://api.alanchand.com/?type=golds&token={ALANCHAND_TOKEN}"
PRICETODAY_URL = "https://api.priceto.day/v1/latest/irr/usd"
PRICETODAY_EUR_URL = "https://api.priceto.day/v1/latest/irr/eur"
PRICETODAY_IQD_URL = "https://api.priceto.day/v1/latest/irr/iqd"
PRICETODAY_KWD_URL = "https://api.priceto.day/v1/latest/irr/kwd"
PRICETODAY_AED_URL = "https://api.priceto.day/v1/latest/irr/aed"

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


def _deep_find_currency_price(data, symbol: str):
    """
    به‌صورت بازگشتی در هر ساختار JSON دنبال قیمت یک ارز (مثل usd یا eur) می‌گردد.
    چون فرمت دقیق APIهای مختلف فرق دارد، این تابع چند الگوی رایج را پوشش می‌دهد.
    """
    symbol = symbol.lower()
    price_keys = ("price", "value", "sell", "rate", "amount", "Price")

    if isinstance(data, dict):
        keys_lower = {str(k).lower(): k for k in data.keys()}

        # حالت ۱: کلیدی دقیقاً به اسم ارز وجود دارد (مثلاً usd یا eur)
        if symbol in keys_lower:
            val = data[keys_lower[symbol]]
            if isinstance(val, dict):
                for pk in price_keys:
                    for k2 in val:
                        if str(k2).lower() == pk.lower():
                            try:
                                return float(val[k2])
                            except (TypeError, ValueError):
                                pass
            else:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass

        # حالت ۲: این دیکشنری خودش یک آیتم با symbol/name_en برابر ارز مدنظر است
        item_symbol = str(
            data.get("symbol") or data.get("Symbol") or data.get("name_en") or ""
        ).lower()
        if item_symbol == symbol:
            for pk in price_keys:
                for k2 in data:
                    if str(k2).lower() == pk.lower():
                        try:
                            return float(data[k2])
                        except (TypeError, ValueError):
                            pass

        # در غیر این صورت، بازگشتی در مقادیر دیگر بگرد
        for v in data.values():
            result = _deep_find_currency_price(v, symbol)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _deep_find_currency_price(item, symbol)
            if result is not None:
                return result

    return None


def _deep_find_usd_price(data):
    """نگه‌داشته شده برای سازگاری با کدهای قبلی؛ معادل جستجوی usd است."""
    return _deep_find_currency_price(data, "usd")


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


def fetch_eur_price() -> str:
    errors = []

    # منبع ۱: AlanChand
    if ALANCHAND_TOKEN:
        try:
            resp = requests.get(ALANCHAND_URL, headers=REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            price = _deep_find_currency_price(data, "eur")
            if price:
                return _format_price(price)
            errors.append("alanchand: قیمت یورو در پاسخ پیدا نشد")
        except Exception as e:
            errors.append(f"alanchand: {e}")
    else:
        errors.append("alanchand: ALANCHAND_TOKEN ست نشده")

    # منبع ۲ (پشتیبان): priceto.day
    try:
        resp = requests.get(PRICETODAY_EUR_URL, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        number = _try_parse_plain_number(resp.text)
        if number is None:
            data = resp.json()
            if isinstance(data, (int, float)):
                number = float(data)
            else:
                number = _deep_find_currency_price(data, "eur")
        if number:
            return _format_price(number)
        errors.append("priceto.day: قیمت یورو در پاسخ پیدا نشد")
    except Exception as e:
        errors.append(f"priceto.day: {e}")

    raise RuntimeError(" | ".join(errors))


def fetch_iqd_price() -> str:
    """
    قیمت دینار عراق چون به‌ازای ۱ دینار خیلی کوچک است، به‌ازای ۱۰۰۰ دینار محاسبه می‌شود.
    """
    errors = []

    # منبع ۱: AlanChand
    if ALANCHAND_TOKEN:
        try:
            resp = requests.get(ALANCHAND_URL, headers=REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            price = _deep_find_currency_price(data, "iqd")
            if price:
                return _format_price(price * 1000) + " (هر ۱۰۰۰ دینار)"
            errors.append("alanchand: قیمت دینار عراق در پاسخ پیدا نشد")
        except Exception as e:
            errors.append(f"alanchand: {e}")
    else:
        errors.append("alanchand: ALANCHAND_TOKEN ست نشده")

    # منبع ۲ (پشتیبان): priceto.day
    try:
        resp = requests.get(PRICETODAY_IQD_URL, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        number = _try_parse_plain_number(resp.text)
        if number is None:
            data = resp.json()
            if isinstance(data, (int, float)):
                number = float(data)
            else:
                number = _deep_find_currency_price(data, "iqd")
        if number:
            return _format_price(number * 1000) + " (هر ۱۰۰۰ دینار)"
        errors.append("priceto.day: قیمت دینار عراق در پاسخ پیدا نشد")
    except Exception as e:
        errors.append(f"priceto.day: {e}")

    raise RuntimeError(" | ".join(errors))


def fetch_kwd_price() -> str:
    """دینار کویت برخلاف عراق، ارزش بالایی دارد (در حد چند برابر دلار)، پس به‌ازای ۱ دینار حساب می‌شود."""
    errors = []

    # منبع ۱: AlanChand
    if ALANCHAND_TOKEN:
        try:
            resp = requests.get(ALANCHAND_URL, headers=REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            price = _deep_find_currency_price(data, "kwd")
            if price:
                return _format_price(price)
            errors.append("alanchand: قیمت دینار کویت در پاسخ پیدا نشد")
        except Exception as e:
            errors.append(f"alanchand: {e}")
    else:
        errors.append("alanchand: ALANCHAND_TOKEN ست نشده")

    # منبع ۲ (پشتیبان): priceto.day
    try:
        resp = requests.get(PRICETODAY_KWD_URL, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        number = _try_parse_plain_number(resp.text)
        if number is None:
            data = resp.json()
            if isinstance(data, (int, float)):
                number = float(data)
            else:
                number = _deep_find_currency_price(data, "kwd")
        if number:
            return _format_price(number)
        errors.append("priceto.day: قیمت دینار کویت در پاسخ پیدا نشد")
    except Exception as e:
        errors.append(f"priceto.day: {e}")

    raise RuntimeError(" | ".join(errors))


def fetch_aed_price() -> str:
    """درهم امارات به‌ازای ۱ درهم محاسبه می‌شود."""
    errors = []

    # منبع ۱: AlanChand
    if ALANCHAND_TOKEN:
        try:
            resp = requests.get(ALANCHAND_URL, headers=REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            price = _deep_find_currency_price(data, "aed")
            if price:
                return _format_price(price)
            errors.append("alanchand: قیمت درهم در پاسخ پیدا نشد")
        except Exception as e:
            errors.append(f"alanchand: {e}")
    else:
        errors.append("alanchand: ALANCHAND_TOKEN ست نشده")

    # منبع ۲ (پشتیبان): priceto.day
    try:
        resp = requests.get(PRICETODAY_AED_URL, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        number = _try_parse_plain_number(resp.text)
        if number is None:
            data = resp.json()
            if isinstance(data, (int, float)):
                number = float(data)
            else:
                number = _deep_find_currency_price(data, "aed")
        if number:
            return _format_price(number)
        errors.append("priceto.day: قیمت درهم در پاسخ پیدا نشد")
    except Exception as e:
        errors.append(f"priceto.day: {e}")

    raise RuntimeError(" | ".join(errors))


def build_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    username = context.bot.username
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 مشاهده نرخ آنلاین", url=f"https://t.me/{username}")],
        [InlineKeyboardButton("➕ اضافه کردن به گروه", url=f"https://t.me/{username}?startgroup=true")],
    ])


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

    try:
        eur = fetch_eur_price()
        parts.append(f"💶 قیمت یورو = {eur}")
    except Exception:
        logger.exception("خطا در دریافت قیمت یورو (گزارش کامل)")

    try:
        iqd = fetch_iqd_price()
        parts.append(f"🇮🇶 قیمت دینار عراق = {iqd}")
    except Exception:
        logger.exception("خطا در دریافت قیمت دینار عراق (گزارش کامل)")

    try:
        kwd = fetch_kwd_price()
        parts.append(f"🇰🇼 قیمت دینار کویت = {kwd}")
    except Exception:
        logger.exception("خطا در دریافت قیمت دینار کویت (گزارش کامل)")

    try:
        aed = fetch_aed_price()
        parts.append(f"🇦🇪 قیمت درهم امارات = {aed}")
    except Exception:
        logger.exception("خطا در دریافت قیمت درهم (گزارش کامل)")

    if not parts:
        return "متاسفانه الان نتونستم قیمت‌ها رو بگیرم. کمی بعد دوباره تلاش کن."

    return "\n\n".join(parts)


async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_full_report(), reply_markup=build_keyboard(context))


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_usd_price()
        await update.message.reply_text(
            f"💵 قیمت دلار آزاد:\n{price}", reply_markup=build_keyboard(context)
        )
    except Exception:
        logger.exception("خطا در دریافت قیمت")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def euro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_eur_price()
        await update.message.reply_text(
            f"💶 قیمت یورو:\n{price}", reply_markup=build_keyboard(context)
        )
    except Exception:
        logger.exception("خطا در دریافت قیمت یورو")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت یورو رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def iqd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_iqd_price()
        await update.message.reply_text(
            f"🇮🇶 قیمت دینار عراق:\n{price}", reply_markup=build_keyboard(context)
        )
    except Exception:
        logger.exception("خطا در دریافت قیمت دینار عراق")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت دینار عراق رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def kwd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_kwd_price()
        await update.message.reply_text(
            f"🇰🇼 قیمت دینار کویت:\n{price}", reply_markup=build_keyboard(context)
        )
    except Exception:
        logger.exception("خطا در دریافت قیمت دینار کویت")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت دینار کویت رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def aed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = fetch_aed_price()
        await update.message.reply_text(
            f"🇦🇪 قیمت درهم امارات:\n{price}", reply_markup=build_keyboard(context)
        )
    except Exception:
        logger.exception("خطا در دریافت قیمت درهم")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت درهم رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def dinar_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇮🇶 دینار عراق", callback_data="dinar_iqd"),
            InlineKeyboardButton("🇰🇼 دینار کویت", callback_data="dinar_kwd"),
        ]
    ])
    await update.message.reply_text("کدوم دینار رو می‌خوای؟", reply_markup=keyboard)


async def dinar_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "dinar_iqd":
        try:
            price = fetch_iqd_price()
            text = f"🇮🇶 قیمت دینار عراق:\n{price}"
        except Exception:
            logger.exception("خطا در دریافت قیمت دینار عراق")
            text = "متاسفانه الان نتونستم قیمت رو بگیرم. کمی بعد دوباره تلاش کن."
    elif query.data == "dinar_kwd":
        try:
            price = fetch_kwd_price()
            text = f"🇰🇼 قیمت دینار کویت:\n{price}"
        except Exception:
            logger.exception("خطا در دریافت قیمت دینار کویت")
            text = "متاسفانه الان نتونستم قیمت رو بگیرم. کمی بعد دوباره تلاش کن."
    else:
        return

    await query.edit_message_text(text, reply_markup=build_keyboard(context))


async def gold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = fetch_gold_prices()
        await update.message.reply_text(text, reply_markup=build_keyboard(context))
    except Exception:
        logger.exception("خطا در دریافت قیمت طلا")
        await update.message.reply_text(
            "متاسفانه الان نتونستم قیمت طلا رو بگیرم. کمی بعد دوباره تلاش کن."
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! برای دیدن قیمت لحظه‌ای، فقط یکی از کلمه‌های «دلار»، «یورو»، «دینار»، «درهم» یا «طلا» رو برام بنویس."
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
    elif "یورو" in text:
        await euro_command(update, context)
    elif "دینار" in text:
        await dinar_choice_handler(update, context)
    elif "درهم" in text:
        await aed_command(update, context)
    elif "دلار" in text:
        await price_command(update, context)


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    این تابع وقتی کسی توی هر چتی بنویسه @یوزرنیم_بات و بعد یه کلمه، فعال می‌شه
    (نیاز به فعال بودن Inline Mode در BotFather دارد: /setinline).

    نکته مهم: تابع‌های fetch_*_price از کتابخونه requests (synchronous) استفاده
    می‌کنن. اگه مستقیم await بشن، کل event loop بات قفل می‌شه و باعث می‌شه
    کلاینت تلگرام بی‌نهایت در حال لودینگ بمونه. برای همین هرکدوم رو توی
    یه thread جدا (asyncio.to_thread) اجرا می‌کنیم و یه سقف زمانی (timeout)
    کوتاه براشون می‌گذاریم.
    """
    query = (update.inline_query.query or "").strip().lower()

    # هر گزینه: (کلیدواژه‌ها، عنوان نمایشی، تابع گرفتن قیمت، پیشوند پیام)
    options = [
        ("usd", ["دلار", "dollar", "usd"], "💵 قیمت دلار", fetch_usd_price, "💵 قیمت دلار:\n"),
        ("eur", ["یورو", "euro", "eur"], "💶 قیمت یورو", fetch_eur_price, "💶 قیمت یورو:\n"),
        ("gold", ["طلا", "gold"], "🏆 نرخ انواع طلا", fetch_gold_prices, ""),
        ("iqd", ["عراق", "iqd"], "🇮🇶 دینار عراق", fetch_iqd_price, "🇮🇶 قیمت دینار عراق:\n"),
        ("kwd", ["کویت", "kwd"], "🇰🇼 دینار کویت", fetch_kwd_price, "🇰🇼 قیمت دینار کویت:\n"),
        ("aed", ["درهم", "aed", "امارات"], "🇦🇪 درهم امارات", fetch_aed_price, "🇦🇪 قیمت درهم امارات:\n"),
    ]

    matched = [
        opt for opt in options
        if query == "" or any(query in kw.lower() or kw.lower() in query for kw in opt[1])
    ]

    async def safe_fetch(fetch_fn):
        try:
            # اجرای تابع بلاکینگ توی یه thread جدا، با سقف ۸ ثانیه‌ای
            return await asyncio.wait_for(asyncio.to_thread(fetch_fn), timeout=8)
        except Exception:
            logger.exception("خطا در inline query")
            return None

    # همه قیمت‌های منطبق رو به‌صورت موازی (نه پشت‌سرهم) بگیر
    fetched = await asyncio.gather(*[safe_fetch(opt[3]) for opt in matched])

    results = []
    for (key, keywords, title, fetch_fn, prefix), price_text in zip(matched, fetched):
        if price_text is None:
            message_text = "متاسفانه الان نتونستم قیمت رو بگیرم. کمی بعد دوباره تلاش کن."
        else:
            message_text = price_text if prefix == "" else f"{prefix}{price_text}"

        description = message_text.split("\n")[0][:60]
        results.append(
            InlineQueryResultArticle(
                id=key,
                title=title,
                description=description,
                input_message_content=InputTextMessageContent(message_text),
            )
        )

    await update.inline_query.answer(results, cache_time=20)


def main():
    if BOT_TOKEN == "PUT-YOUR-TOKEN-HERE":
        raise SystemExit("لطفاً اول BOT_TOKEN رو با توکن واقعی بات خودت جایگزین کن.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(dinar_callback_handler, pattern="^dinar_"))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("بات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
