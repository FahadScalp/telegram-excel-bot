import os
import re
from decimal import Decimal, ROUND_HALF_UP
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# معاملات قابلة للتغيير من Env Vars
A_MULT = Decimal(os.getenv("A_MULT", "0.323"))
B_MULT = Decimal(os.getenv("B_MULT", "0.786"))

def count_decimals_str(s: str | None) -> int:
    if not s:
        return 0
    if "." in s:
        return len(s.split(".", 1)[1])
    if "," in s:
        return len(s.split(",", 1)[1])
    return 0

def dround(x: Decimal, places: int) -> str:
    q = Decimal(10) ** -places
    return str(x.quantize(q, rounding=ROUND_HALF_UP))

def parse_high_low_and_order(text: str):
    """
    يرجّع: high, low, order_hint, places
    places = أكبر عدد منازل عشرية في الأرقام المدخلة (حد أدنى 3 إذا كلاهما صحيحان).
    """
    raw = text
    t = text.lower().replace("قمه", "قمة")
    # دعم الفاصلة العشرية
    t = re.sub(r'(\d),(\d)', r'\1.\2', t)

    # أماكن الكلمات لتخمين الترتيب
    low_pos  = re.search(r'(قاع|low)\b', t)
    high_pos = re.search(r'(قمة|high|هاي|top|peak|h)\b', t)
    order_hint = None
    if low_pos and high_pos:
        order_hint = "low-first" if low_pos.start() < high_pos.start() else "high-first"

    # أرقام مرتبطة بالكلمات (نحتفظ بالنص الأصلي لحساب المنازل)
    hm = re.search(r'([-+]?\d+(?:[\.,]\d+)?)[^\n\r]*?(?:قمة|high|هاي|top|peak|h)\b', text, flags=re.I)
    lm = re.search(r'([-+]?\d+(?:[\.,]\d+)?)[^\n\r]*?(?:قاع|low)\b', text, flags=re.I)

    high_s = hm.group(1) if hm else None
    low_s  = lm.group(1) if lm else None
    high = Decimal(high_s.replace(",", ".")) if high_s else None
    low  = Decimal(low_s.replace(",", "."))  if low_s  else None
    high_dec = count_decimals_str(high_s)
    low_dec  = count_decimals_str(low_s)

    if high is None or low is None:
        # fallback: أول رقمين
        nums = re.findall(r'[-+]?\d+(?:\.\d+)?', t)
        if len(nums) >= 2:
            a_s, b_s = nums[0], nums[1]
            a = Decimal(a_s); b = Decimal(b_s)
            a_dec = count_decimals_str(a_s); b_dec = count_decimals_str(b_s)
            if order_hint is None:
                order_hint = "low-first" if a < b else "high-first"
            if order_hint == "low-first":
                low, high = a, b
                low_dec, high_dec = a_dec, b_dec
            else:
                high, low = a, b
                high_dec, low_dec = a_dec, b_dec

    max_dec = max(high_dec or 0, low_dec or 0)
    places = max_dec if max_dec > 0 else 3  # إن كانت أعداد صحيحة، استخدم 3 منازل بشكل افتراضي
    return high, low, order_hint, places

def compute_sell_from_high_low(high: Decimal, low: Decimal):
    diff = high - low
    k = high - (diff * A_MULT)                 # Sell Limit
    tp = low - (high - k) * B_MULT             # TP
    return k, tp

def compute_buy_from_low_high(low: Decimal, high: Decimal):
    diff = high - low
    k = low + (diff * A_MULT)                  # Buy Limit
    tp = high + (k - low) * B_MULT             # TP
    return k, tp

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل قمة/قاع بأي ترتيب:\n"
        "- قمة ثم قاع ⇒ SELL\n"
        "- قاع ثم قمة ⇒ BUY\n"
        "الأرقام تُعرض بعدد منازل مثل مدخلاتك (حد أدنى 3 إذا كانت صحيحة)."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    high, low, order_hint, places = parse_high_low_and_order(text)
    if high is None or low is None:
        await update.message.reply_text("لم أفهم القيم. مثال:\n1.16506 قمة\n1.16439 قاع")
        return

    mode = "buy" if (order_hint == "low-first" or (order_hint is None and low < high)) else "sell"

    if mode == "sell":
        k, tp = compute_sell_from_high_low(high, low)
        title = "Sell Limit"
    else:
        k, tp = compute_buy_from_low_high(low, high)
        title = "Buy Limit"

    d = lambda v: dround(v, places)
    # سطر واحد فقط، بدون (K)
    reply = f"✅ {mode.upper()} → {title}={d(k)} | TP={d(tp)}"
    await update.message.reply_text(reply)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN env var.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
