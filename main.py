import os
import asyncio
import aiosqlite
from tweepy.asynchronous import AsyncClient
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Токены из переменных Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

twitter = AsyncClient(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)

DB_NAME = "subscriptions.db"

class Form(StatesGroup):
    waiting_username = State()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                username TEXT,
                PRIMARY KEY (user_id, username)
            );
            CREATE TABLE IF NOT EXISTS last_tweets (
                username TEXT PRIMARY KEY,
                tweet_id TEXT
            );
        """)
        await db.commit()

async def add_subscription(user_id: int, username: str):
    username = username.lower().lstrip('@')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO subscriptions VALUES (?, ?)", (user_id, username))
        await db.commit()

async def remove_subscription(user_id: int, username: str):
    username = username.lower().lstrip('@')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM subscriptions WHERE user_id = ? AND username = ?", (user_id, username))
        await db.commit()

async def get_subscriptions(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT username FROM subscriptions WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def get_all_usernames():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT DISTINCT username FROM subscriptions")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def get_subscribers(username: str):
    username = username.lower().lstrip('@')
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT user_id FROM subscriptions WHERE username = ?", (username,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def set_last_tweet(username: str, tweet_id: str):
    username = username.lower().lstrip('@')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO last_tweets (username, tweet_id) VALUES (?, ?)", (username, tweet_id))
        await db.commit()

async def get_last_tweet(username: str):
    username = username.lower().lstrip('@')
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT tweet_id FROM last_tweets WHERE username = ?", (username,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def check_new_tweets():
    usernames = await get_all_usernames()
    if not usernames:
        return

    for username in usernames:
        try:
            user = await twitter.get_user(username=username)
            if not user.data:
                continue

            tweets = await twitter.get_users_tweets(
                user.data.id,
                max_results=5,
                tweet_fields=["id"],
                exclude=["retweets", "replies"]
            )

            if not tweets.data:
                continue

            last_id = await get_last_tweet(username)
            new_tweets = [tweet for tweet in tweets.data if not last_id or tweet.id > int(last_id)]

            if new_tweets:
                subscribers = await get_subscribers(username)
                for tweet in reversed(new_tweets):
                    message = f"Новый твит от @{username}\n\n{tweet.text}\n\nhttps://x.com/{username}/status/{tweet.id}"
                    for user_id in subscribers:
                        await bot.send_message(user_id, message, disable_web_page_preview=True)
                    await set_last_tweet(username, str(tweet.id))

        except Exception as e:
            print(f"Ошибка для @{username}: {e}")

# === Хендлеры ===
@dp.message(Command("start"))
async def start_handler(message: Message):
    kb = [
        [types.KeyboardButton(text="Добавить аккаунт")],
        [types.KeyboardButton(text="Мои подписки")],
        [types.KeyboardButton(text="Удалить аккаунт")],
        [types.KeyboardButton(text="/check")]
    ]
    await message.answer(
        "Привет! Я уведомляю о новых твитах.\n"
        "Добавь аккаунты — при посте пришлю уведомление.\n\n"
        "Команды:\n/check — ручная проверка",
        reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )

@dp.message(Command("check"))
async def check_handler(message: Message):
    await message.answer("Проверяю новые твиты...")
    await check_new_tweets()
    await message.answer("Проверка завершена!")

@dp.message(lambda m: m.text == "Добавить аккаунт")
async def add_start(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_username)
    await message.answer("Пришли username (без @):", reply_markup=types.ReplyKeyboardRemove())

@dp.message(Form.waiting_username)
async def add_done(message: Message, state: FSMContext):
    username = message.text.strip().lstrip('@').lower()
    if not re.match(r"^[a-zA-Z0-9_]{1,15}$", username):
        await message.answer("Неправильный username. Пришли заново.")
        return
    await add_subscription(message.from_user.id, username)
    await message.answer(f"Добавил @{username} в отслеживание!")
    await state.clear()

@dp.message(lambda m: m.text == "Мои подписки")
async def my_subscriptions(message: Message):
    subs = await get_subscriptions(message.from_user.id)
    if not subs:
        await message.answer("Ты ни на кого не подписан.")
    else:
        text = "\n".join(f"• @{u}" for u in subs)
        await message.answer(f"Твои подписки:\n{text}")

@dp.message(lambda m: m.text == "Удалить аккаунт")
async def del_start(message: Message):
    subs = await get_subscriptions(message.from_user.id)
    if not subs:
        await message.answer("Нечего удалять.")
        return
    kb = [[types.KeyboardButton(text=f"@{u}")] for u in subs]
    await message.answer("Кого удалить?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: m.text and m.text.startswith("@"))
async def del_done(message: Message):
    username = message.text.strip().lstrip('@').lower()
    await remove_subscription(message.from_user.id, username)
    await message.answer(f"Удалил @{username} из отслеживания.")

# === Автоматическая проверка ===
async def auto_check():
    await init_db()
    while True:
        await check_new_tweets()
        await asyncio.sleep(45)  # Каждые 45 секунд

async def main():
    asyncio.create_task(auto_check())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
