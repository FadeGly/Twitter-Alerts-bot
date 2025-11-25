import os
import asyncio
from tweepy.asynchronous import AsyncClient
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram import F

# Токены из переменных окружения Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Tweepy клиент (без прокси — Railway в США, Twitter не блокирует)
twitter = AsyncClient(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)

# Хранилище в памяти (Railway перезапускается редко, но можно потом прикрутить Redis)
subscriptions = {}  # user_id → set(usernames)
last_tweet_ids = {} # username → tweet_id

# === Команды ===
@dp.message(Command("start"))
async def start(msg: Message):
    subscriptions[msg.from_user.id] = set()
    kb = [
        [types.KeyboardButton(text="Добавить аккаунт")],
        [types.KeyboardButton(text="Мои подписки")],
        [types.KeyboardButton(text="Удалить аккаунт")],
    ]
    await msg.answer(
        "Привет! Я твой личный уведомлятор твитов\n"
        "Добавляй любые аккаунты — при новом твите сразу пришлю",
        reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )

@dp.message(F.text == "Добавить аккаунт")
async def add_start(msg: Message):
    await msg.answer("Пришли username без @: ", reply_markup=types.ReplyKeyboardRemove())

@dp.message(F.text.regexp(r'^[a-zA-Z0-9_]{1,15}$'))
async def add_done(msg: Message):
    username = msg.text.strip().lower()
    subscriptions.setdefault(msg.from_user.id, set()).add(username)
    await msg.answer(f"Добавил @{username} — теперь буду следить ✓")

@dp.message(F.text == "Мои подписки")
async def my_list(msg: Message):
    users = subscriptions.get(msg.from_user.id, set())
    if not users:
        await msg.answer("Ты пока ни на кого не подписан")
    else:
        text = "\n".join(f"• @{u}" for u in sorted(users))
        await msg.answer(f"<b>Ты следишь за:</b>\n{text}", parse_mode="HTML")

@dp.message(F.text == "Удалить аккаунт")
async def del_start(msg: Message):
    users = subscriptions.get(msg.from_user.id, set())
    if not users:
        await msg.answer("Нечего удалять")
        return
    kb = [[types.KeyboardButton(text=f"@{u}")] for u in sorted(users)]
    await msg.answer("Кого убрать из отслеживания?", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.text.startswith("@"))
async def del_done(msg: Message):
    username = msg.text.lstrip("@").lower()
    if username in subscriptions.get(msg.from_user.id, set()):
        subscriptions[msg.from_user.id].remove(username)
        await msg.answer(f"Больше не слежу за @{username}")
    else:
        await msg.answer("Ты и так не следишь за этим аккаунтом")

# === Проверка новых твитов ===
async def check_tweets():
    if not subscriptions:
        return

    for user_id, usernames in subscriptions.items():
        for username in list(usernames):  # копия, чтобы не было ошибок при изменении
            try:
                user = await twitter.get_user(username=username)
                if not user.data:
                    continue

                tweets = await twitter.get_users_tweets(
                    user.data.id,
                    max_results=5,
                    tweet_fields=["created_at"],
                    exclude=["retweets", "replies"]
                )

                if not tweets.data:
                    continue

                for tweet in tweets.data:
                    tid = str(tweet.id)
                    if username not in last_tweet_ids or tid > last_tweet_ids[username]:
                        last_tweet_ids[username] = tid
                        link = f"https://x.com/{username}/status/{tid}"
                        await bot.send_message(
                            user_id,
                            f"Новый твит от @{username}\n\n{tweet.text}\n\n{link}",
                            disable_web_page_preview=True
                        )
            except Exception as e:
                print(f"Ошибка @{username}: {e}")
        await asyncio.sleep(1)  # вежливость к API

# === Фоновая задача ===
async def scheduler():
    await asyncio.sleep(10)
    while True:
        await check_tweets()
        await asyncio.sleep(40)  # проверка каждые 40 секунд

# === Запуск ===
async def main():
    asyncio.create_task(scheduler())
    print("Бот запущен на Railway!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
