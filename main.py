import os
import asyncio
import aiosqlite
import feedparser
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_NAME = "data.db"

class Form(StatesGroup):
    waiting = State()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS subs (user_id INTEGER, username TEXT, PRIMARY KEY(user_id, username));
            CREATE TABLE IF NOT EXISTS last_entries (username TEXT PRIMARY KEY, entry_id TEXT);
        ''')
        await db.commit()

async def add_sub(uid, name):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO subs VALUES (?, ?)", (uid, name))
        await db.commit()

async def del_sub(uid, name):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM subs WHERE user_id=? AND username=?", (uid, name))
        await db.commit()

async def get_my_subs(uid):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT username FROM subs WHERE user_id=?", (uid,))
        return [r[0] for r in await cur.fetchall()]

async def get_all_usernames():
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT DISTINCT username FROM subs")
        return [r[0] for r in await cur.fetchall()]

async def get_subscribers(name):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT user_id FROM subs WHERE username=?", (name,))
        return [r[0] for r in await cur.fetchall()]

async def set_last_entry(name, entry_id):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO last_entries VALUES (?, ?)", (name, entry_id))
        await db.commit()

async def get_last_entry(name):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT entry_id FROM last_entries WHERE username=?", (name,))
        row = await cur.fetchone()
        return row[0] if row else None

async def check_rss_feeds():
    usernames = await get_all_usernames()
    if not usernames:
        print("Нет подписок")
        return

    new_count = 0
    for username in usernames:
        try:
            # Twiiit.com — ротация Nitter, работает в 2025
            rss_url = f"https://twiiit.com/{username}/rss"
            feed = feedparser.parse(rss_url)
            print(f"@{username}: получено {len(feed.entries)} твитов в фиде")
            if not feed.entries:
                print(f"Пустой фид для @{username} — инстанс Nitter упал")
                continue

            last_entry = await get_last_entry(username)
            new_entries = []
            for entry in feed.entries:
                entry_id = entry.id or entry.link
                if not last_entry or entry_id != last_entry:
                    new_entries.append(entry)
                else:
                    break

            if new_entries:
                subscribers = await get_subscribers(username)
                print(f"@{username}: найдено {len(new_entries)} новых твитов для {len(subscribers)} пользователей")
                new_count += len(new_entries)
                for entry in reversed(new_entries):
                    title = entry.title or "Новый твит"
                    link = entry.link
                    msg = f"Новый твит от @{username}\n\n{title}\n\n{link}"
                    for uid in subscribers:
                        await bot.send_message(uid, msg, disable_web_page_preview=True)
                    await set_last_entry(username, entry.id or entry.link)
            else:
                print(f"@{username}: новых твитов нет")

        except Exception as e:
            print(f"Ошибка @{username}: {e}")

    print(f"Общая проверка завершена: {new_count} новых твитов")

# === Хендлеры ===
@dp.message(Command("start"))
async def start(m: types.Message):
    kb = [
        [types.KeyboardButton(text="Добавить")],
        [types.KeyboardButton(text="Список")],
        [types.KeyboardButton(text="Удалить")],
        [types.KeyboardButton(text="/check")]
    ]
    await m.answer("Бот уведомлений о твитах (Twiiit — ротация Nitter, обновление 1–5 мин)", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(Command("check"))
async def manual(m: types.Message):
    await m.answer("Проверяю...")
    await check_rss_feeds()
    await m.answer("Готово! Если новых твитов нет, проверь логи в Railway.")

@dp.message(lambda m: m.text == "Добавить")
async def add_s(m: types.Message, state: FSMContext):
    await state.set_state(Form.waiting)
    await m.answer("Пришли username без @:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(Form.waiting)
async def add_d(m: types.Message, state: FSMContext):
    u = m.text.strip().lstrip('@')
    if not re.match(r"^[a-zA-Z0-9_]{1,15}$", u):
        await m.answer("Неправильный username")
        return
    await add_sub(m.from_user.id, u)
    await m.answer(f"Добавил @{u}")
    await state.clear()

@dp.message(lambda m: m.text == "Список")
async def lst(m: types.Message):
    s = await get_my_subs(m.from_user.id)
    await m.answer("Ты следишь за:\n" + "\n".join(f"• @{x}" for x in s) if s else "Пусто")

@dp.message(lambda m: m.text == "Удалить")
async def del_s(m: types.Message):
    s = await get_my_subs(m.from_user.id)
    if not s: await m.answer("Нечего удалять"); return
    kb = [[types.KeyboardButton(text=f"@{x}")] for x in s]
    await m.answer("Кого удалить?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: m.text and m.text.startswith("@"))
async def del_d(m: types.Message):
    u = m.text.lstrip("@")
    await del_sub(m.from_user.id, u)
    await m.answer(f"Удалил @{u}")

# === Авто ===
async def scheduler():
    await asyncio.sleep(5)
    await init_db()
    while True:
        await check_rss_feeds()
        await asyncio.sleep(120)

async def main():
    asyncio.create_task(scheduler())
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
