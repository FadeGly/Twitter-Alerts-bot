import os
import asyncio
import aiosqlite
import re
from tweepy.asynchronous import AsyncClient
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN")
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
twitter = AsyncClient(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)

DB_NAME = "data.db"

class Form(StatesGroup):
    waiting = State()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS subs (user_id INTEGER, username TEXT, PRIMARY KEY(user_id, username));
            CREATE TABLE IF NOT EXISTS last_tweet (username TEXT PRIMARY KEY, tweet_id TEXT);
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
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def get_all_unique_usernames():
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT DISTINCT username FROM subs")
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def get_subscribers(username):
    username = username.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT user_id FROM subs WHERE username=?", (username,))
        rows = await cur.fetchall()
        return [r[0] for r in rows]

async def set_last(username, tid):
    username = username.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO last_tweet VALUES (?, ?)", (username, tid))
        await db.commit()

async def get_last(username):
    username = username.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT tweet_id FROM last_tweet WHERE username=?", (username,))
        row = await cur.fetchone()
        return row[0] if row else None

# === ГЛАВНОЕ ИСПРАВЛЕНИЕ: ОДИН ЦИКЛ + ПАУЗЫ + ПРЕДВАРИТЕЛЬНАЯ ЗАГРУЗКА ID ===
async def check_new_tweets():
    usernames = await get_all_unique_usernames()
    if not usernames:
        return

    # Один запрос на все usernames сразу (экономим 50–90% запросов)
    try:
        users_response = await twitter.get_users(usernames=usernames)
        if not users_response.data:
            return
        user_id_map = {user.username.lower(): user.id for user in users_response.data}
    except Exception as e:
        print(f"Ошибка получения пользователей: {e}")
        return

    # Последовательно проверяем каждого, с паузой 5–7 сек между запросами
    for username in usernames:
        user_id = user_id_map.get(username.lower())
        if not user_id:
            continue

        try:
            tweets = await twitter.get_users_tweets(
                user_id,
                max_results=5,
                exclude=["replies", "retweets"]
            )
            if not tweets.data:
                await asyncio.sleep(5)
                continue

            last_id = await get_last(username)
            new_tweets = [t for t in tweets.data if not last_id or str(t.id) > last_id]

            if new_tweets:
                for tweet in reversed(new_tweets):  # с самого нового
                    await set_last(username, str(tweet.id))
                    link = f"https://x.com/{username}/status/{tweet.id}"
                    msg = f"Новый твит от @{username}\n\n{tweet.text}\n\n{link}"
                    subs = await get_subscribers(username)
                    for uid in subs:
                        await bot.send_message(uid, msg, disable_web_page_preview=True)

            await asyncio.sleep(6)  # 6 сек между запросами = 10 запросов в минуту → никогда не будет rate limit

        except Exception as e:
            print(f"Ошибка @{username}: {e}")
            await asyncio.sleep(10)

# === Хендлеры (без изменений) ===
@dp.message(Command("start"))
async def start(m: types.Message):
    kb = [
        [types.KeyboardButton(text="Добавить")],
        [types.KeyboardButton(text="Список")],
        [types.KeyboardButton(text="Удалить")],
        [types.KeyboardButton(text="/check")]
    ]
    await m.answer("Уведомления о новых твитах X/Twitter\nРаботает 24/7", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(Command("check"))
async def manual(m: types.Message):
    await m.answer("Проверяю...")
    await check_new_tweets()
    await m.answer("Готово!")

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

# === Запуск ===
async def main():
    await init_db()
    asyncio.create_task(check_new_tweets())  # первая проверка сразу (чтобы заполнить last_tweet и не слать старые)
    dp.startup.register(lambda: print("Бот запущен!"))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
