import os
import re
from decimal import Decimal, ROUND_HALF_UP
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# === Config via env vars ===
A_MULT = Decimal(os.getenv("A_MULT", "0.323"))   # معامل الدخول
B_MULT = Decimal(os.getenv("B_MULT", "0.786"))   # معامل الهدف
DECIMALS = int(os.getenv("DECIMALS", "9"))       # عدد الخانات العشرية

def dround(x: Decimal, places=DECIMALS) -> str:
    q = Decimal(10) ** -places
    return str(x.quantize(q, rounding=ROUND_HALF_UP))

def parse_high_low_and_order(text: str):
    t = text.lower().replace("قمه", "قمة")
    t = re.sub(r'(\d),(\d)', r'\1.\2', t)

    low_pos  = re.search(r'(قاع|low)\b', t)
    high_pos = re.search(r'(قمة|high|هاي|top|peak|h)\b', t)
    order_hint = None
    if low_pos and high_pos:
        order_hint = "low-first" if low_pos.start() < high_pos.start() else "high-first"

    high_match = re.search(r'([-+]?\d+(?:\.\d+)?)[^\n\r]*?(?:قمة|high|هاي|top|peak|h)\b', t)
    low_match  = re.search(r'([-+]?\d+(?:\.\d+)?)[^\n\r]*?(?:قاع|low)\b', t)

    high = Decimal(high_match.group(1)) if high_match else None
    low  = Decimal(low_match.group(1))  if low_match  else None

    if high is None or low is None:
        nums = re.findall(r'[-+]?\d+(?:\.\d+)?', t)
        if len(nums) >= 2:
            a = Decimal(nums[0]); b = Decimal(nums[1])
            if order_hint is None:
                order_hint = "low-first" if a < b else "high-first"
            if order_hint == "low-first":
                low, high = a, b
            elif order_hint == "high-first":
                high, low = a, b
            else:
                high, low = max(a, b), min(a, b)
    return high, low, order_hint

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

def excel_formulas_sell(high: Decimal, low: Decimal, k: Decimal):
    f_k  = "=I-((I-J)*0.323)"
    f_tp = "=J-(I-K)*0.786"
    fv_k  = f"={high}-(({high}-{low})*0.323)"
    fv_tp = f"={low}-({high}-{k})*0.786"
    return f_k, f_tp, fv_k, fv_tp

def excel_formulas_buy(low: Decimal, high: Decimal, k: Decimal):
    f_k  = "=I+((J-I)*0.323)"
    f_tp = "=J-(I-K)*0.786"
    fv_k  = f"={low}+(({high}-{low})*0.323)"
    fv_tp = f"={high}-({low}-{k})*0.786"
    return f_k, f_tp, fv_k, fv_tp

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل قمة/قاع بأي ترتيب:\n"
        "- قمة ثم قاع ⇒ Sell Limit + TP\n"
        "- قاع ثم قمة ⇒ Buy Limit + TP\n"
        "أو رقمين فقط: الأصغر أولًا ⇒ Buy، الأكبر أولًا ⇒ Sell.\n"
        "يمكن تعديل A_MULT و B_MULT و DECIMALS من متغيّرات البيئة."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    high, low, order_hint = parse_high_low_and_order(text)
    if high is None or low is None:
        await update.message.reply_text("لم أفهم القيم. مثال:\n1.16506 قمة\n1.16439 قاع\nأو: 1.16439 قاع\n1.16506 قمة")
        return

    if order_hint == "low-first":
        mode = "buy"
    elif order_hint == "high-first":
        mode = "sell"
    else:
        mode = "buy" if low < high else "sell"

    if mode == "sell":
        k, tp = compute_sell_from_high_low(high, low)
        f_k, f_tp, fv_k, fv_tp = excel_formulas_sell(high, low, k)
        title = "Sell Limit (K)"
    else:
        k, tp = compute_buy_from_low_high(low, high)
        f_k, f_tp, fv_k, fv_tp = excel_formulas_buy(low, high, k)
        title = "Buy Limit (K)"

    d = lambda v: dround(v, DECIMALS)
    reply = (
        f"📊 High(I)={d(high)} | Low(J)={d(low)}\n"
        f"✅ {mode.upper()} → {title}={d(k)} | TP={d(tp)}\n\n"
        f"🧮 Excel:\n{f_k}\n{f_tp}\n\n"
        f"🧮 With values:\n{fv_k}\n{fv_tp}\n"
    )
    await update.message.reply_text(reply)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN env var.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
