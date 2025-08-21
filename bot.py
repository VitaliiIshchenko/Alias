import os, re, random, asyncio
from collections import deque
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI, APIError, RateLimitError

BUTTON = "🔁 Новое слово"
kb = ReplyKeyboardMarkup([[KeyboardButton(BUTTON)]], resize_keyboard=True)

# --- utils ---
def valid_ru(w: str) -> bool:
    return bool(re.fullmatch(r"[а-яё]{3,16}", (w or "").strip()))

FALLBACK = ["лампа","мяч","рыба","машина","книга","телефон","лимон","снег","трава","мост"]

# --- init ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not BOT_TOKEN: raise RuntimeError("Нет BOT_TOKEN в .env")
if not OPENAI_API_KEY: raise RuntimeError("Нет OPENAI_API_KEY в .env")

client = OpenAI(api_key=OPENAI_API_KEY)

used_words: set[str] = set()
cache: deque[str] = deque()
lock = asyncio.Lock()

SYSTEM_PROMPT = (
    "Ты — генератор слов для Alias на русском. "
    "Верни только слова: обычные русские существительные, кириллица, нижний регистр, 3–16 букв. "
    "Без нумерации, без дефисов, без пояснений."
)

async def gpt_batch_words(n: int = 12) -> list[str]:
    """Запросить у GPT пачку слов и отфильтровать."""
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Сгенерируй {n} разных слов, каждое с новой строки."}
            ],
            temperature=1.0,
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "")
        words = []
        for line in text.splitlines():
            w = line.strip().split()[0] if line.strip() else ""
            if valid_ru(w):
                words.append(w)
        # удаляем дубли
        uniq = []
        s = set()
        for w in words:
            if w not in s:
                s.add(w); uniq.append(w)
        return uniq
    except (RateLimitError, APIError):
        return []
    except Exception:
        return []

async def refill_cache_if_needed():
    """Пополнить кэш, если он пустой/маленький."""
    if len(cache) >= 3:
        return
    batch = await gpt_batch_words(15)
    # отбрасываем уже выданные
    fresh = [w for w in batch if w not in used_words and w not in cache]
    for w in fresh:
        cache.append(w)
    # если GPT не дал — добавим фолбэк
    if not cache:
        for w in FALLBACK:
            if w not in used_words:
                cache.append(w)

# --- handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    used_words.clear()
    cache.clear()
    await refill_cache_if_needed()
    await update.message.reply_text(
        "Привет! Жми кнопку, чтобы получить слово (кэш ускоряет выдачу). /newgame — сброс.",
        reply_markup=kb
    )

async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    used_words.clear()
    cache.clear()
    await refill_cache_if_needed()
    await update.message.reply_text("♻️ История очищена. Поехали заново!", reply_markup=kb)

async def give_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with lock:
        # если кэш пуст — пополняем (асинхронно подождём)
        if not cache:
            await refill_cache_if_needed()
        # выдаём мгновенно из кэша
        if cache:
            w = cache.popleft()
            # гарантируем отсутствие повторов в сессии
            if w in used_words:
                await give_word(update, context)
                return
            used_words.add(w)
            await update.message.reply_text(f"Твоё слово: {w}", reply_markup=kb)
            # пополняем кэш «в фоне», не блокируя ответ
            asyncio.create_task(refill_cache_if_needed())
            return

        # совсем нет слов
        await update.message.reply_text("⚠️ Слова закончились. Нажми /newgame.", reply_markup=kb)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("word", give_word))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(BUTTON)}$"), give_word))
    print("Bot with cache is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
