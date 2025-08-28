import os
import re
from decimal import Decimal, ROUND_HALF_UP
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# معاملات الدخول/الهدف قابلة للتعديل عبر متغيرات البيئة
A_MULT = Decimal(os.getenv("A_MULT", "0.323"))   # معامل دخول (ارتداد من المدى)
B_MULT = Decimal(os.getenv("B_MULT", "0.786"))   # معامل الهدف TP

# إعدادات وقف الخسارة (تبدّل من Env Vars على Render)
# SL_MODE: "pct_range" (افتراضي) أو "at_extreme" أو "fixed"
SL_MODE = os.getenv("SL_MODE", "pct_range")
# إذا SL_MODE=pct_range → SL = High + range*s (للبيع) أو Low - range*s (للشراء)
SL_PCT = Decimal(os.getenv("SL_PCT", "0.1"))     # نسبة من المدى (مثال 0.1 = 10%)
# إذا SL_MODE=fixed → SL = High + offset (للبيع) أو Low - offset (للشراء)
SL_OFFSET = Decimal(os.getenv("SL_OFFSET", "0"))

# ----------------- أدوات تنسيق ومدخلات -----------------
def count_decimals_str(s: str | None) -> int:
    """يحتسب عدد المنازل العشرية كما كتبها المستخدم (يدعم . أو ,)."""
    if not s:
        return 0
    if "." in s:
        return len(s.split(".", 1)[1])
    if "," in s:
        return len(s.split(",", 1)[1])
    return 0

def dround(x: Decimal, places: int) -> str:
    """تقريب/تنسيق بقيم Decimal بعدد منازل يساوي مدخلات المستخدم."""
    q = Decimal(10) ** -places  # لو places=0 → q=1 → رقم صحيح بدون كسور
    return str(x.quantize(q, rounding=ROUND_HALF_UP))

def parse_high_low_and_order(text: str):
    """
    يرجّع: high, low, order_hint, places
      - order_hint: "low-first" لو القاع مذكور قبل القمة نصيًا، "high-first" عكسه، أو None
      - places: أكبر عدد منازل عشرية ظهر في الأرقام المدخلة (بدون فرض حد أدنى)
    """
    t_lower = text.lower().replace("قمه", "قمة")
    # استخدم النص الأصلي لاستخراج الأرقام للحفاظ على الدقة (.,)
    # تحديد ترتيب الكلمات (قاع/قمة) من النص الصغير
    low_pos  = re.search(r'(قاع|low)\b', t_lower)
    high_pos = re.search(r'(قمة|high|هاي|top|peak|h)\b', t_lower)
    order_hint = None
    if low_pos and high_pos:
        order_hint = "low-first" if low_pos.start() < high_pos.start() else "high-first"

    # أرقام مرتبطة بالكلمات (نحتفظ بالنص لاحتساب المنازل)
    hm = re.search(r'([-+]?\d+(?:[\.,]\d+)?)[^\n\r]*?(?:قمة|high|هاي|top|peak|h)\b', text, flags=re.I)
    lm = re.search(r'([-+]?\d+(?:[\.,]\d+)?)[^\n\r]*?(?:قاع|low)\b', text, flags=re.I)

    high_s = hm.group(1) if hm else None
    low_s  = lm.group(1) if lm else None
    high = Decimal(high_s.replace(",", ".")) if high_s else None
    low  = Decimal(low_s.replace(",", "."))  if low_s  else None
    high_dec = count_decimals_str(high_s)
    low_dec  = count_decimals_str(low_s)

    # إن لم نجد مع الكلمات → خذ أول رقمين فقط
    if high is None or low is None:
        nums = re.findall(r'[-+]?\d+(?:[\.,]\d+)?', text)
        if len(nums) >= 2:
            a_s, b_s = nums[0], nums[1]
            a = Decimal(a_s.replace(",", "."))
            b = Decimal(b_s.replace(",", "."))
            a_dec = count_decimals_str(a_s)
            b_dec = count_decimals_str(b_s)
            if order_hint is None:
                order_hint = "low-first" if a < b else "high-first"
            if order_hint == "low-first":
                low, high = a, b
                low_dec, high_dec = a_dec, b_dec
            else:
                high, low = a, b
                high_dec, low_dec = a_dec, b_dec

    max_dec = max(high_dec or 0, low_dec or 0)
    places = max_dec  # لا نفرض 3 منازل؛ إن كانت صحيحة تطلع بدون كسور
    return high, low, order_hint, places

def parse_stoploss(text: str):
    """يلتقط وقف الخسارة من الرسالة: (وقف|ستوب|SL) 123.45"""
    m = re.search(r'(وقف|ستوب|sl)\s*[:=]?\s*([-+]?\d+(?:[\.,]\d+)?)', text, flags=re.I)
    if m:
        s = m.group(2)
        return Decimal(s.replace(",", ".")), count_decimals_str(s)
    return None, None

# ----------------- الحسابات -----------------
def compute_sell_from_high_low(high: Decimal, low: Decimal):
    # Sell Limit = High - ((High - Low) * A_MULT)
    diff = high - low
    k = high - (diff * A_MULT)
    # TP = Low - (High - SellLimit) * B_MULT
    tp = low - (high - k) * B_MULT
    return k, tp

def compute_buy_from_low_high(low: Decimal, high: Decimal):
    # Buy Limit = Low + ((High - Low) * A_MULT)
    diff = high - low
    k = low + (diff * A_MULT)
    # TP = High + (BuyLimit - Low) * B_MULT
    tp = high + (k - low) * B_MULT
    return k, tp

def compute_sl(mode: str, high: Decimal, low: Decimal):
    """حساب SL حسب الإعدادات (إذا لم يحدده المستخدم في الرسالة)."""
    if SL_MODE == "at_extreme":
        return high if mode == "sell" else low
    if SL_MODE == "fixed":
        return (high + SL_OFFSET) if mode == "sell" else (low - SL_OFFSET)
    # pct_range (افتراضي)
    rng = high - low
    return (high + rng * SL_PCT) if mode == "sell" else (low - rng * SL_PCT)

# ----------------- Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل قمة/قاع بأي ترتيب:\n"
        "- قمة ثم قاع ⇒ SELL\n"
        "- قاع ثم قمة ⇒ BUY\n"
        "سأرجع سطرًا واحدًا فقط: Limit | TP | SL\n"
        "يمكنك أيضًا كتابة وقف الخسارة مثل: «وقف 113252»."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    high, low, order_hint, places = parse_high_low_and_order(text)

    if high is None or low is None:
        await update.message.reply_text("لم أفهم القيم. مثال:\n113153 قمة\n112160 قاع\nأو: 112160 قاع\n113153 قمة")
        return

    mode = "buy" if (order_hint == "low-first" or (order_hint is None and low < high)) else "sell"

    if mode == "sell":
        entry, tp = compute_sell_from_high_low(high, low)
        title = "Sell Limit"
    else:
        entry, tp = compute_buy_from_low_high(low, high)
        title = "Buy Limit"

    # وقف الخسارة: من الرسالة أو محسوب من الإعدادات
    sl_from_text, sl_decimals = parse_stoploss(text)
    sl = sl_from_text if sl_from_text is not None else compute_sl(mode, high, low)

    # تنسيق الخرج بدقة مدخلاتك؛ ولو كتبت SL بنفسك نستخدم دقته
    d_entry = dround(entry, places)
    d_tp    = dround(tp, places)
    sl_places = sl_decimals if sl_decimals is not None else places
    d_sl    = dround(sl, sl_places)

    reply = f"✅ {mode.upper()} → {title}={d_entry} | TP={d_tp} | SL={d_sl}"
    await update.message.reply_text(reply)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN env var.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # drop_pending_updates=True لتفريغ التحديثات القديمة عند الإقلاع
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
