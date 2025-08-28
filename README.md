# Telegram Excel Bot on Render (Polling Worker)
1) ارفع الملفات إلى GitHub.
2) render.com → New → Blueprint → اربط الريبو (يحوي render.yaml) → Apply.
3) أضف TELEGRAM_BOT_TOKEN كـ Env Var (توكن جديد من BotFather).
4) Deploy. لا حاجة لـ Webhook لأن الكود يستخدم polling.
يمكن تعديل A_MULT/B_MULT/DECIMALS من Env Vars.