import asyncio
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, BaseMiddleware, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from deep_translator import GoogleTranslator
import random
import google.genai as genai
from google.genai import types as genai_types
from cachetools import TTLCache
from typing import Any, Awaitable, Callable, Dict

# Налаштування конфігурації: Токени для Telegram та Gemini API
TELEGRAM_BOT_TOKEN = "8580426946:AAGLsxImSa-oayIVtahgW6gqAUM5hiZeC-Y"
GEMINI_API_KEY = "AIzaSyAAgvf3S7_bDhvPJBa8xgb5uTOnOR9VzwE"

# Ініціалізація бота та диспетчера
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Підключення до бази даних та створення курсору
conn = sqlite3.connect("words.db")
cursor = conn.cursor()

# Створення таблиці користувачів, якщо вона не існує
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    start_date TEXT,
    last_active TEXT
)
""")

# Створення таблиці слів користувачів, якщо вона не існує
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_words (
    user_id INTEGER,
    word TEXT,
    translation TEXT,
    language TEXT,
    usage_count INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, word, language)
)
""")
conn.commit()

# Головне меню клавіатури
main_kb = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="/add_word"), types.KeyboardButton(text="/all_words")],
        [types.KeyboardButton(text="/practice"), types.KeyboardButton(text="/delete_word")],
        [types.KeyboardButton(text="/stats"), types.KeyboardButton(text="/word_of_day")],
        [types.KeyboardButton(text="/AI"), types.KeyboardButton(text="/exit")]
    ],
    resize_keyboard=True
)


# Визначення станів (FSM) для процесу додавання слова
class AddWord(StatesGroup):
    waiting_for_word = State()
    waiting_for_language = State()
    waiting_for_translation = State()


# Визначення станів для видалення слова
class DeleteWord(StatesGroup):
    waiting_for_word = State()


# Визначення станів для режиму тренування
class PracticeWord(StatesGroup):
    waiting_for_language = State()
    waiting_for_answer = State()


# Визначення станів для перегляду слів
class ViewWords(StatesGroup):
    waiting_for_language = State()


# Визначення станів для взаємодії зі штучним інтелектом
class AIHelper(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_language = State()


# Новий стан для Слова Дня
class WordOfDayState(StatesGroup):
    waiting_for_language = State()


# Middleware для обмеження частоти запитів (Anti-spam)
class ThrottlingMiddleware(BaseMiddleware):

    def __init__(self, throttle_time: int = 1):
        self.cache = TTLCache(maxsize=10000, ttl=throttle_time)

    async def __call__(
            self,
            handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]],
            event: types.Message,
            data: Dict[str, Any]
    ) -> Any:

        if not isinstance(event, types.Message) or not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id

        if user_id in self.cache:
            return
        else:
            self.cache[user_id] = True
            return await handler(event, data)


# Текст з описом команд для користувача
COMMANDS_TEXT = (
    "Доступні команди:\n"
    "/add_word – додати нове слово 📚\n"
    "/delete_word – видалити слово ❌\n"
    "/all_words – список усіх слів 📝\n"
    "/practice – тренування 🎯\n"
    "/stats – ваша статистика 📊\n"
    "/word_of_day – слово дня 🌟\n"
    "/AI – допомога ШІ 🤖\n"
    "/exit – вихід з режиму 🚪"
)

# Список підтримуваних мов
SUPPORTED_LANGUAGES = ["English", "German", "French", "Polish", "Spanish", "Italian"]


# Функція реєстрації нового користувача в базі даних
def add_user(user_id, username):
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, start_date, last_active) VALUES (?, ?, ?, ?)",
            (user_id, username, datetime.now().isoformat(), datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in add_user: {e}")


# Оновлення часу останньої активності користувача
def update_last_active(user_id):
    try:
        cursor.execute(
            "UPDATE users SET last_active=? WHERE user_id=?",
            (datetime.now().isoformat(), user_id)
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in update_last_active: {e}")


# Додавання нового слова до словника користувача
def add_word_to_db(user_id, word, translation, language):
    try:
        cursor.execute("SELECT 1 FROM user_words WHERE user_id=? AND word=? AND language=?", (user_id, word, language))
        if cursor.fetchone():
            return False
        cursor.execute(
            "INSERT INTO user_words (user_id, word, translation, language, usage_count) VALUES (?, ?, ?, ?, 0)",
            (user_id, word, translation, language)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except sqlite3.Error as e:
        print(f"Database error in add_word_to_db: {e}")
        return False


# Видалення слова з бази даних
def delete_word_from_db(user_id, word):
    try:
        cursor.execute("DELETE FROM user_words WHERE user_id=? AND word=?", (user_id, word))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in delete_word_from_db: {e}")


# Отримання списку слів користувача (всіх або конкретної мови)
def get_user_words(user_id, language=None):
    try:
        if language is None:
            cursor.execute("SELECT word, translation, language, usage_count FROM user_words WHERE user_id=?",
                           (user_id,))
        else:
            cursor.execute(
                "SELECT word, translation, language, usage_count FROM user_words WHERE user_id=? AND language=?",
                (user_id, language))
        return cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Database error in get_user_words: {e}")
        return []


# Збільшення лічильника успішних повторень слова
def increment_usage_count(user_id, word, language=None):
    try:
        if language:
            cursor.execute(
                "UPDATE user_words SET usage_count = usage_count + 1 WHERE user_id=? AND word=? AND language=?",
                (user_id, word, language))
        else:
            cursor.execute("UPDATE user_words SET usage_count = usage_count + 1 WHERE user_id=? AND word=?",
                           (user_id, word))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in increment_usage_count: {e}")


# Розрахунок рівня користувача на основі вивчених слів
def get_user_level(user_id):
    words = get_user_words(user_id)
    total_correct = sum([u for w, t, l, u in words])
    level = total_correct // 10 + 1
    return level


# Налаштування клієнта ШІ
try:
    pass
except AttributeError:
    print("Warning: 'genai.configure' not found. Trying manual client.")
    pass

client = genai.Client(api_key=GEMINI_API_KEY)


# Функція для отримання пояснення слова від ШІ
def get_ai_explanation(content, language_of_word):
    print(f"GenAI: Обробка запиту '{content}' (мова слова: '{language_of_word}')...")

    system_prompt = (
        f"Ти — помічник, який пояснює значення слів. "
        f"Поясни значення слова '{content}', яке належить до мови: {language_of_word}. "
        "Це важливо, оскільки слова можуть мати різне значення в різних мовах (омографи). "
        "Додай 1-2 приклади використання. "
        "ЗАВЖДИ відповідай українською мовою. "
        "ВАЖЛИВО: У твоїй відповіді не повинно бути жодних символів Markdown, особливо зірочок (*)."
    )

    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        config=config,
        contents=content,
    )

    clean_text = response.text.replace("*", "")
    return clean_text


# Обробник команди /start: Реєстрація та привітання
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    add_user(message.from_user.id, message.from_user.username)
    update_last_active(message.from_user.id)
    await state.clear()
    welcome_text = (
        "👋 Привіт!\nЯ ваш словниковий бот.\n\n"
        "Я допомагаю вивчати нові слова:\n"
        "- Додавай слова та їх переклад 📚\n"
        "- Видаляй слова ❌\n"
        "- Переглядай свій словник 📝\n"
        "- Практикуй переклади 🎯\n"
        "- Допомога ШІ 🤖\n\n"
        f"{COMMANDS_TEXT}"
    )
    await message.answer(welcome_text, reply_markup=main_kb)


# Обробник команди /exit: Вихід з будь-якого стану FSM
@dp.message(Command("exit"))
async def cmd_exit(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("🚪 Зараз жоден з режимів не активний.", reply_markup=main_kb)
        return

    await state.clear()
    await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)


# Початок процесу додавання слова
@dp.message(Command("add_word"))
async def cmd_add_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    await state.set_state(AddWord.waiting_for_word)
    await message.answer("✏️ Введіть слово для додавання на мові яка вас цікавить (або /exit):", reply_markup=main_kb)


# Обробка введеного слова для додавання
@dp.message(AddWord.waiting_for_word)
async def process_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()

    if text.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return
    if text.startswith("/"):
        await message.answer("❌ Будь ласка, спочатку завершіть додавання слова або натисніть /exit.")
        return

    word = text
    await state.update_data(word=word)

    keyboard = [[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AddWord.waiting_for_language)
    await message.answer("🌍 Оберіть мову слова:", reply_markup=lang_kb)


# Обробка вибору мови та збереження слова
# 1. Показуємо автопереклад і даємо вибір
@dp.message(AddWord.waiting_for_language)
async def process_language(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    language = message.text.strip()

    if language.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    if language not in SUPPORTED_LANGUAGES:
        await message.answer("❌ Невідома мова. Виберіть зі списку або /exit.")
        return

    await state.update_data(language=language)
    data = await state.get_data()
    word = data.get("word")

    # Автопереклад
    try:
        translator = GoogleTranslator(source='auto', target="uk")
        auto_translation = translator.translate(word)
    except Exception:
        auto_translation = "Не вдалося перекласти"

    await state.update_data(auto_translation=auto_translation)

    # Клавіатура: Зберегти або Вийти (користувач може ввести текст вручну)
    keyboard = [
        [types.KeyboardButton(text=f"Зберегти: {auto_translation}")],
        [types.KeyboardButton(text="/exit")]
    ]
    trans_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Переходимо до нового стану - перевірки перекладу
    await state.set_state(AddWord.waiting_for_translation)
    await message.answer(
        f"🔍 Автопереклад: **{auto_translation}**\n\n"
        "Натисніть кнопку, щоб зберегти його, АБО **напишіть свій переклад** вручну:",
        reply_markup=trans_kb, parse_mode="Markdown"
    )


# 2. Зберігаємо фінальний варіант переклада
@dp.message(AddWord.waiting_for_translation)
async def process_custom_translation(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_input = message.text.strip()
    user_id = message.from_user.id

    if user_input.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    data = await state.get_data()
    word = data.get("word")
    language = data.get("language")
    auto_translation = data.get("auto_translation")

    final_translation = auto_translation if user_input.startswith("Зберегти:") else user_input

    added = add_word_to_db(user_id, word, final_translation, language)

    if not added:
        await message.answer(f"⚠️ Слово '{word}' вже є у вашому словнику.", reply_markup=main_kb)
    else:
        await message.answer(
            f"✅ Додано: {word} — {final_translation} ({language})\n\nВведіть наступне слово або /exit.",
            reply_markup=main_kb)

    await state.set_state(AddWord.waiting_for_word)


# Початок процесу видалення слова
@dp.message(Command("delete_word"))
async def cmd_delete_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    await state.set_state(DeleteWord.waiting_for_word)
    await message.answer("🗑️ Введіть слово для видалення (або /exit):", reply_markup=main_kb)


# Обробка видалення слова
@dp.message(DeleteWord.waiting_for_word)
async def process_delete_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()
    user_id = message.from_user.id

    if text.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    if text.startswith("/"):
        await message.answer("❌ Будь ласка, спочатку завершіть видалення або натисніть /exit.")
        return

    words_in_db = [w for w, t, l, u in get_user_words(user_id)]

    if text in words_in_db:
        delete_word_from_db(user_id, text)
        await message.answer(f"🗑️ Слово '{text}' видалено.\n\nВведіть наступне слово для видалення (або /exit):",
                             reply_markup=main_kb)
    else:
        await message.answer(f"❌ Слова '{text}' немає в словнику.\n\nВведіть інше слово (або /exit):",
                             reply_markup=main_kb)


# Початок перегляду всіх слів
@dp.message(Command("all_words"))
async def cmd_all_words(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_id = message.from_user.id
    words = get_user_words(user_id)
    if not words:
        await message.answer("📭 Ваш словник порожній.", reply_markup=main_kb)
        return

    languages = sorted(list(set([l for _, _, l, _ in words if l is not None])))
    if not languages:
        words_list = "\n".join([f"{w} — {t}" for w, t, l, u in words])
        await message.answer(f"📝 Ваші слова:\n{words_list}", reply_markup=main_kb)
        return

    keyboard = [[types.KeyboardButton(text=l)] for l in languages]
    keyboard.append([types.KeyboardButton(text="Усі мови")])
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(ViewWords.waiting_for_language)
    await message.answer("🌐 Оберіть мову, щоб переглянути слова:", reply_markup=lang_kb)


# Відображення слів для вибраної мови
@dp.message(ViewWords.waiting_for_language)
async def process_view_language(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang_choice = message.text.strip()

    if lang_choice.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    if lang_choice == "Усі мови":
        words = get_user_words(user_id)
    else:
        words = get_user_words(user_id, language=lang_choice)

    if not words:
        await message.answer("📭 Словник порожній для цієї мови.", reply_markup=main_kb)
    else:
        text = f"📝 Слова ({lang_choice}):\n"
        for w, t, l, u in words:
            text += f"{w} — {t} (мова: {l}, успіхів: {u})\n"

        if len(text) > 4096:
            await message.answer(f"📝 Слова ({lang_choice}):\n... (занадто багато слів для відображення)",
                                 reply_markup=main_kb)
        else:
            await message.answer(text, reply_markup=main_kb)

    await state.clear()


# Початок режиму практики
@dp.message(Command("practice"))
async def cmd_practice(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_id = message.from_user.id
    words = get_user_words(user_id)
    if not words:
        await message.answer("📭 Ваш словник порожній. Додайте слова через /add_word.", reply_markup=main_kb)
        return

    languages = sorted(list(set([l for _, _, l, _ in words if l is not None])))
    keyboard = [[types.KeyboardButton(text=l)] for l in languages]
    keyboard.append([types.KeyboardButton(text="Усі мови")])
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.update_data(all_practice_words=words)
    await state.set_state(PracticeWord.waiting_for_language)
    await message.answer("🎯 Оберіть мову для практики (або 'Усі мови'):", reply_markup=lang_kb)


# Вибір мови для практики та генерація списку слів
@dp.message(PracticeWord.waiting_for_language)
async def practice_choose_lang(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()

    if text.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    data = await state.get_data()
    all_words = data.get("all_practice_words", [])

    if text == "Усі мови":
        practice_list = [(w, t, l, u) for w, t, l, u in all_words]
    else:
        practice_list = [(w, t, l, u) for w, t, l, u in all_words if l == text]

    if not practice_list:
        await message.answer("📭 Немає слів для цієї мови.", reply_markup=main_kb)
        await state.clear()
        return

    random.shuffle(practice_list)

    level = get_user_level(message.from_user.id)
    practice_count = min(len(practice_list), 5 + level)

    final_practice_list = practice_list[:practice_count]

    await state.update_data(practice_list=final_practice_list, practice_index=0)
    await state.set_state(PracticeWord.waiting_for_answer)

    w, t, l, u = final_practice_list[0]
    await message.answer(f"✏️ (1/{len(final_practice_list)}) Введіть переклад слова: {t} (мова: {l})",
                         reply_markup=main_kb)


# Перевірка відповіді користувача в режимі практики
@dp.message(PracticeWord.waiting_for_answer)
async def process_practice(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_id = message.from_user.id
    data = await state.get_data()
    practice_list = data.get("practice_list", [])
    idx = data.get("practice_index", 0)

    if not practice_list:
        await state.clear()
        await message.answer("📭 Немає слів для практики.", reply_markup=main_kb)
        return

    text = message.text.strip()
    if text.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    correct_word = practice_list[idx][0]
    correct_translation = practice_list[idx][1]
    correct_language = practice_list[idx][2]

    if text.lower() == correct_word.lower():
        increment_usage_count(user_id, correct_word, correct_language)
        await message.answer(f"✅ Правильно! {correct_translation} = {correct_word} 🎉", reply_markup=main_kb)
    else:
        await message.answer(f"❌ Неправильно. Правильне слово: {correct_word} 📚", reply_markup=main_kb)

    idx += 1
    if idx >= len(practice_list):
        await state.clear()
        await message.answer("🏁 Практика завершена. Додайте нові слова або оберіть інший режим.", reply_markup=main_kb)
        return
    else:
        await state.update_data(practice_index=idx)
        next_w, next_t, next_l, next_u = practice_list[idx]
        await message.answer(f"✏️ ({idx + 1}/{len(practice_list)}) Введіть переклад слова: {next_t} (мова: {next_l})",
                             reply_markup=main_kb)


# Відображення статистики користувача
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    words = get_user_words(user_id)
    total_words = len(words)
    total_correct = sum([u for w, t, l, u in words])
    level = get_user_level(user_id)

    lang_stats = {}
    for w, t, l, u in words:
        if l not in lang_stats:
            lang_stats[l] = 0
        lang_stats[l] += 1

    stats_text = f"📊 Статистика користувача:\n" \
                 f"- Кількість слів у словнику: {total_words}\n" \
                 f"- Правильних відповідей: {total_correct}\n" \
                 f"- Ваш рівень: {level} 🏆\n\n" \
                 "Слова по мовах:\n"

    for lang, count in lang_stats.items():
        stats_text += f"- {lang}: {count} сл.\n"

    await message.answer(stats_text, reply_markup=main_kb)


# Слово дня з ШІ
@dp.message(Command("word_of_day"))
async def cmd_word_of_day(message: types.Message, state: FSMContext):
    # Вибір мови для генерації
    keyboard = [[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(WordOfDayState.waiting_for_language)
    await message.answer("🌟 Оберіть мову для нового слова:", reply_markup=lang_kb)


@dp.message(WordOfDayState.waiting_for_language)
async def process_word_of_day_lang(message: types.Message, state: FSMContext):
    lang = message.text.strip()
    user_id = message.from_user.id

    if lang.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    if lang not in SUPPORTED_LANGUAGES:
        await message.answer("❌ Невідома мова. Виберіть зі списку.")
        return

    await message.answer(f"⏳ Аналізую ваш рівень та шукаю слово ({lang})...")

    level = get_user_level(user_id)
    if level <= 5:
        difficulty = "A1 (Beginner) - базові слова"
    elif level <= 15:
        difficulty = "A2-B1 (Elementary/Intermediate) - розмовні слова"
    else:
        difficulty = "B2-C1 (Upper Intermediate) - просунуті слова або ідіоми"

    user_words = get_user_words(user_id, lang)
    known_words = [w[0] for w in user_words]
    known_list_str = ", ".join(known_words[-50:])

    prompt = (
        f"Згенеруй 1 (одне) слово або коротку фразу мовою {lang} для рівня {difficulty}. "
        f"Важливо: Це слово НЕ повинно бути у цьому списку: [{known_list_str}]. "
        f"Формат відповіді суворо: 'Слово - Переклад'. Переклад українською. "
        f"Без зайвого тексту."
    )

    try:
        config = genai_types.GenerateContentConfig(temperature=0.9)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            config=config,
            contents=prompt
        )
        result = response.text.strip().replace("*", "")

        if " - " in result:
            new_word, translation = result.split(" - ", 1)
        else:
            new_word, translation = result, "Переклад не знайдено"

        await state.update_data(new_word=new_word, translation=translation, lang=lang)

        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="➕ Додати до словника", callback_data="add_wod")]
        ])

        await message.answer(
            f"🌟 Слово дня: {new_word}\n"
            f"🇺🇦 Переклад: {translation}\n"
            f"📊 Рівень: {difficulty.split(' - ')[0]}",
            reply_markup=kb
        )

    except Exception as e:
        print(f"AI Error: {e}")
        await message.answer("⚠️ Не вдалося згенерувати слово. Спробуйте пізніше.", reply_markup=main_kb)
        await state.clear()


@dp.callback_query(F.data == "add_wod", WordOfDayState.waiting_for_language)
async def add_word_of_day_to_db(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_word = data.get("new_word")
    translation = data.get("translation")
    lang = data.get("lang")

    added = add_word_to_db(callback.from_user.id, new_word, translation, lang)

    if added:
        await callback.message.edit_text(
            f"✅ Чудово! Слово {new_word} додано до словника."
        )
    else:
        await callback.message.edit_text("⚠️ Це слово вже є у вашому словнику.")

    await state.clear()


# Початок взаємодії з ШІ
@dp.message(Command("AI"))
async def cmd_ai(message: types.Message, state: FSMContext):
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Що ви хочете, щоб я пояснив? Введіть слово або фразу (або /exit):",reply_markup=main_kb)


# Отримання запиту для ШІ
@dp.message(AIHelper.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    text = message.text.strip()

    if text.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    if text.startswith("/"):
        await message.answer("❌ Будь ласка, спочатку введіть запит для ШІ або натисніть /exit.")
        return

    await state.update_data(prompt=text)

    languages_list = SUPPORTED_LANGUAGES + ["Українська"]
    keyboard = [[types.KeyboardButton(text=l)] for l in languages_list]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AIHelper.waiting_for_language)
    await message.answer("🌍 Це слово з якої мови? (Це допоможе зрозуміти контекст):", reply_markup=lang_kb)


# Обробка мови запиту та отримання відповіді від ШІ
@dp.message(AIHelper.waiting_for_language)
async def process_ai_language(message: types.Message, state: FSMContext):
    language_of_word = message.text.strip()

    if language_of_word.lower() == '/exit':
        await state.clear()
        await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=main_kb)
        return

    valid_languages = SUPPORTED_LANGUAGES + ["Українська"]
    if language_of_word not in valid_languages:
        await message.answer("❌ Невідома мова. Виберіть зі списку або /exit.")
        return

    data = await state.get_data()
    prompt = data.get("prompt")

    if not prompt:
        await state.clear()
        await message.answer("⚠️ Сталася помилка, запит не знайдено. Спробуйте /AI ще раз.", reply_markup=main_kb)
        return

    await message.answer("🤖 Оброблюю ваш запит...", reply_markup=main_kb)

    try:
        response = get_ai_explanation(prompt, language_of_word)
        await message.answer(f"🤖 Ось пояснення:\n\n{response}", reply_markup=main_kb)

    except Exception as e:
        await message.answer(f"{str(e)}", reply_markup=main_kb)

    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Що ще пояснити? Введіть слово або фразу (або /exit):",
                         reply_markup=main_kb)


# Обробник невідомих команд або тексту
@dp.message()
async def unknown_command(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await message.answer(
            "❌ Незрозуміла відповідь. Будь ласка, дотримуйтесь інструкцій або натисніть /exit, щоб вийти з поточного режиму.")
        return

    await message.answer("❌ Невідома команда. Спробуйте одну з доступних:\n" + COMMANDS_TEXT, reply_markup=main_kb)


# Запуск бота
async def main():
    print("Бота запущено")
    dp.message.middleware(ThrottlingMiddleware(throttle_time=1))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())