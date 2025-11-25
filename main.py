import os
import asyncio
import aiosqlite
import re                      # ← ЭТО БЫЛО ЗАБЫТО!
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

DB_NAME = "subscriptions.db"

class Form(StatesGroup):
    waiting_username = State()

# === БАЗА ===
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

async def set_last(name, tid):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO last_tweet VALUES (?, ?)", (name, tid))
        await db.commit()

async def get_last(name):
    name = name.lstrip('@').lower()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT tweet_id FROM last_tweet WHERE username=?", (name,))
        row = await cur.fetchone()
        return row[0] if row else None

# === Проверка твитов ===
async def check_new_tweets():
    usernames = await get_all_usernames()
    for username in usernames:
        try:
            user = await twitter.get_user(username=username)
            if not user.data: continue
            tweets = await twitter.get_users_tweets(user.data.id, max_results=5, exclude=["replies","retweets"])
            if not tweets.data: continue
            last = await get_last(username)
            for tweet in tweets.data:
                if not last or int(tweet.id) > int(last):
                    await set_last(username, str(tweet.id))
                    subs = await get_subscribers(username)
                    msg = f"Новый твит от @{username}\n\n{tweet.text}\n\nhttps://x.com/{username}/status/{tweet.id}"
                    for uid in subs:
                        await bot.send_message(uid, msg, disable_web_page_preview=True)
        except Exception as e:
            print(f"Ошибка @{username}: {e}")
        await asyncio.sleep(1)

# === Хендлеры ===
@dp.message(Command("start"))
async def start(m: types.Message):
    kb = [
        [types.KeyboardButton(text="Добавить аккаунт")],
        [types.KeyboardButton(text="Мои подписки")],
        [types.KeyboardButton(text="Удалить аккаунт")],
        [types.KeyboardButton(text="/check — проверить сейчас")]
    ]
    await m.answer(
        "Привет! Уведомления о новых твитах.\n"
        "Добавляй аккаунты — пришлю сразу же при новом посте.",
        reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )

@dp.message(Command("check"))
async def manual_check(m: types.Message):
    await m.answer("Проверяю прямо сейчас...")
    await check_new_tweets()
    await m.answer("Готово!")

@dp.message(lambda m: m.text == "Добавить аккаунт")
async def add_start(m: types.Message, state: FSMContext):
    await state.set_state(Form.waiting_username)
    await m.answer("Пришли username без @:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(Form.waiting_username)
async def add_done(m: types.Message, state: FSMContext):
    username = m.text.strip()
    if not re.match(r"^[a-zA-Z0-9_]{1,15}$", username):
        await m.answer("Неправильный username. Попробуй ещё раз.")
        return
    await add_sub(m.from_user.id, username)
    await m.answer(f"Добавил @{username} — теперь слежу!")
    await state.clear()

@dp.message(lambda m: m.text == "Мои подписки")
async def my_list(m: types.Message):
    subs = await get_my_subs(m.from_user.id)
    if not subs:
        await m.answer("Ты ни на кого не подписан.")
    else:
        text = "\n".join(f"• @{u}" for u in subs)
        await m.answer(f"Ты следишь за:\n{text}")

@dp.message(lambda m: m.text == "Удалить аккаунт")
async def del_start(m: types.Message):
    subs = await get_my_subs(m.from_user.id)
    if not subs:
        await m.answer("Нечего удалять.")
        return
    kb = [[types.KeyboardButton(text=f"@{u}")] for u in subs]
    await m.answer("Кого убрать?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: m.text and m.text.startswith("@"))
async def del_done(m: types.Message):
    username = m.text.lstrip("@")
    await del_sub(m.from_user.id, username)
    await m.answer(f"Удалил @{username}")

# === Автозапуск ===
async def scheduler():
    await asyncio.sleep(5)
    await init_db()
    while True:
        await check_new_tweets()
        await asyncio.sleep(45)

async def main():
    asyncio.create_task(scheduler())
    print("Бот запущен на Railway и работает 24/7")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
