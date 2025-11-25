import asyncio
import logging
import aiohttp
import aiosqlite
import os
import re

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Токен берётся только из переменной окружения (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Установи переменную TELEGRAM_TOKEN в Railway!")

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DB_NAME = "data.db"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"}

logging.basicConfig(level=logging.INFO)

class AddState(StatesGroup):
    waiting = State()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS subs (user_id INTEGER, username TEXT, PRIMARY KEY (user_id, username));
            CREATE TABLE IF NOT EXISTS last_tweet (username TEXT PRIMARY KEY, tweet_id TEXT);
        """)
        await db.commit()

async def add_user(u): async with aiosqlite.connect(DB_NAME) as db: await db.execute("INSERT OR IGNORE INTO users VALUES (?)", (u,)); await db.commit()
async def add_sub(u, name): name = name.lstrip('@').lower(); async with aiosqlite.connect(DB_NAME) as db: await db.execute("INSERT OR IGNORE INTO subs VALUES (?, ?)", (u, name)); await db.commit()
async def del_sub(u, name): name = name.lstrip('@').lower(); async with aiosqlite.connect(DB_NAME) as db: await db.execute("DELETE FROM subs WHERE user_id=? AND username=?", (u, name)); await db.commit()
async def user_subs(u):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT username FROM subs WHERE user_id=?", (u,))
        return [row[0] for row in await cur.fetchall()]

async def all_usernames():
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT DISTINCT username FROM subs")
        return [row[0] for row in await cur.fetchall()]

async def subscribers(name):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT user_id FROM subs WHERE username=?", (name.lower().lstrip('@'),))
        return [row[0] for row in await cur.fetchall()]

async def set_last(username, tid):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO last_tweet VALUES (?, ?) ON CONFLICT(username) DO UPDATE SET tweet_id=excluded.tweet_id", (username.lower().lstrip('@'), tid))
        await db.commit()

async def get_last(username):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT tweet_id FROM last_tweet WHERE username=?", (username.lower().lstrip('@'),))
        row = await cur.fetchone()
        return row[0] if row else None

async def fetch_tweets(username, session):
    url = f"https://rss.app/feeds/{username.lstrip('@')}.xml"
    try:
        async with session.get(url, headers=HEADERS, timeout=12) as r:
            if r.status != 200: return []
            text = await r.text()
            items = re.findall(r'<item>.*?<title>(.*?)</title>.*?<link>(https://x\.com/' + re.escape(username.lstrip('@')) + r'/status/\d+).*?</item>', text, re.DOTALL)
            return [{"id": l.split("/")[-1], "text": t.replace('<![CDATA[','').replace(']]>','').strip(), "link": l} for t, l in items]
    except:
        return []

async def monitor():
    await init_db()
    async with aiohttp.ClientSession() as s:
        while True:
            names = await all_usernames()
            if not names:
                await asyncio.sleep(60)
                continue
            for name in names:
                tweets = await fetch_tweets(name, s)
                if not tweets: continue
                last = await get_last(name)
                new = [t for t in tweets if not last or int(t["id"]) > int(last)]
                if new:
                    subs = await subscribers(name)
                    for t in reversed(new):
                        msg = f"<b>Новый твит от @{name}</b>\n\n{t['text']}\n\n{t['link']}"
                        for uid in subs:
                            try:
                                await bot.send_message(uid, msg, disable_web_page_preview=True)
                                await asyncio.sleep(0.4)
                            except: pass
                        await set_last(name, t["id"])
            await asyncio.sleep(45)

@dp.message(Command("start"))
async def start(m: Message):
    await add_user(m.from_user.id)
    kb = [[types.KeyboardButton(text="Добавить")], [types.KeyboardButton(text="Список")], [types.KeyboardButton(text="Удалить")]]
    await m.answer("Привет! Мультиюзер-бот уведомлений о твитах X/Twitter\nУ каждого свои подписки ✅", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: m.text == "Добавить")
async def add_begin(m: Message, state: FSMContext):
    await add_user(m.from_user.id)
    await state.set_state(AddState.waiting)
    await m.answer("Пришли @username или просто username:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(AddState.waiting)
async def add_end(m: Message, state: FSMContext):
    name = m.text.strip().lstrip('@')
    if not re.match(r"^[a-zA-Z0-9_]{1,15}$", name):
        await m.answer("Неправильный username")
        return
    await add_sub(m.from_user.id, name)
    await m.answer(f"Подписал на @{name} ✅")
    await state.clear()

@dp.message(lambda m: m.text == "Список")
async def list_subs(m: Message):
    await add_user(m.from_user.id)
    subs = await user_subs(m.from_user.id)
    await m.answer("Твои подписки:\n" + ("\n".join(f"• @{s}" for s in subs) if subs else "Пока пусто"))

@dp.message(lambda m: m.text == "Удалить")
async def del_begin(m: Message):
    subs = await user_subs(m.from_user.id)
    if not subs:
        await m.answer("Нечего удалять")
        return
    kb = [[types.KeyboardButton(text=f"@{s}")] for s in subs] + [[types.KeyboardButton(text="Отмена")]]
    await m.answer("Кого отписать?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: m.text and (m.text.startswith("@") or m.text == "Отмена"))
async def del_end(m: Message):
    if m.text == "Отмена":
        await m.answer("Отменил")
        return
    await del_sub(m.from_user.id, m.text)
    await m.answer(f"Отписал от {m.text}")

async def main():
    asyncio.create_task(monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
