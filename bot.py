
import asyncio
import random
import sqlite3
import pytz
from datetime import datetime # это тоже понадобится для команды "время"
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГУРАЦИЯ ---
TOKEN = "7913689244:AAGFfGKzRSCu7Jbfh7sY4w2KCJqROUNROYs"
ADMIN_ID = (8049948727, 8593794663)
X50_CHAT_ID = -1003592894012 

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("lira_ultimate_v2.db", check_same_thread=False)
cur = conn.cursor()

# 1. Создаем основную таблицу пользователей
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    uid INTEGER PRIMARY KEY, 
    name TEXT, 
    bal INTEGER DEFAULT 10000, 
    played INTEGER DEFAULT 0, 
    won INTEGER DEFAULT 0, 
    daily INTEGER DEFAULT 0,
    reg TEXT, 
    bonus TEXT, 
    last_x50_bet TEXT,
    level INTEGER DEFAULT 1,      -- Добавлено для уровней
    used_limit INTEGER DEFAULT 0   -- Добавлено для суточных лимитов
)''')

# 2. ПРОВЕРКА И ДОБАВЛЕНИЕ КОЛОНОК (если таблица уже была создана ранее без них)
# Этот блок исправит ошибки "no such column"
try:
    cur.execute("ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1")
except: pass

try:
    cur.execute("ALTER TABLE users ADD COLUMN used_limit INTEGER DEFAULT 0")
except: pass

# 3. Таблица админов
cur.execute('''CREATE TABLE IF NOT EXISTS admins (uid INTEGER PRIMARY KEY)''')

# 4. Остальные таблицы
cur.execute('''CREATE TABLE IF NOT EXISTS promo (code TEXT PRIMARY KEY, amount INTEGER, uses INTEGER)''')
cur.execute('''CREATE TABLE IF NOT EXISTS promo_history (uid INTEGER, code TEXT)''')
cur.execute('''CREATE TABLE IF NOT EXISTS x50_history (id INTEGER PRIMARY KEY AUTOINCREMENT, res TEXT)''')

# 5. Казна
cur.execute('''CREATE TABLE IF NOT EXISTS treasury (
    id INTEGER PRIMARY KEY, 
    balance INTEGER DEFAULT 0, 
    reward_per_user INTEGER DEFAULT 100)''')
cur.execute("INSERT OR IGNORE INTO treasury (id, balance, reward_per_user) VALUES (1, 0, 100)")

# 6. История игр
cur.execute("""
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER,
    game_name TEXT,
    bet INTEGER,
    win_amount INTEGER,
    coef REAL,
    date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# --- ЭТОТ БЛОК ИСПРАВИТ ОШИБКУ ---
try:
    cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
    conn.commit()
    print("Колонка username успешно добавлена!")
except Exception as e:
    print(f"Заметка: {e}") # Если она уже есть, просто пойдет дальше
# ---------------------------------

# Добавляем новые колонки
for col in [
    ("bank", "INTEGER DEFAULT 0"), 
    ("reputation", "INTEGER DEFAULT 0"), 
    ("bio", "TEXT DEFAULT 'Пока пусто'"),
    ("hide_bal", "INTEGER DEFAULT 0"),  # 0 - открыт, 1 - скрыт
    ("hide_bank", "INTEGER DEFAULT 0")  # 0 - открыт, 1 - скрыт
]:
    try:
        cur.execute(f"ALTER TABLE users ADD COLUMN {col[0]} {col[1]}")
    except: pass
conn.commit()


# --- СОСТОЯНИЯ ---
class GameStates(StatesGroup):
    mines = State()
    hilo = State()
    toad = State()

class AdminStates(StatesGroup):
    promo_name = State()
    promo_sum = State()
    promo_uses = State()
    mailing_text = State()
    give_money = State() # <-- Не забудьте добавить это 

    # ... твои другие состояния ...
    vik_amount = State()   # Ожидание суммы
    vik_question = State() # Ожидание вопроса
    vik_answer = State()   # Ожидание ответа


    # ... твои прошлые состояния ...
    fast_amount = State() # Ожидание суммы для ФК



    
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_u(uid, name, username=None):
    cur.execute("SELECT * FROM users WHERE uid = ?", (uid,))
    res = cur.fetchone()
    if not res:
        from datetime import datetime
        reg_date = datetime.now().strftime("%d.%m.%Y")
        
        # Если юзернейма нет (бывает в ЛС), ставим "None"
        uname = username.replace("@", "") if username else "None"
        
        # ВНИМАНИЕ: Убедись, что количество колонок (uid, name...) 
        # совпадает с количеством знаков ? (их тут 6)
        try:
            cur.execute("""INSERT INTO users (uid, name, reg, level, used_limit, username) 
                           VALUES (?, ?, ?, ?, ?, ?)""", 
                        (uid, name, reg_date, 1, 0, uname))
            conn.commit()
        except Exception as e:
            print(f"Ошибка при регистрации: {e}")
            
        cur.execute("SELECT * FROM users WHERE uid = ?", (uid,))
        return cur.fetchone()
    return res

def b_num(number):
    """Превращает число в жирный текст с разделителями"""
    return f"<b>{number:,}</b>"

def upd_bal(uid, am):
    cur.execute("UPDATE users SET bal = bal + ?, daily = daily + ? WHERE uid = ?", (am, am if am > 0 else 0, uid))
    conn.commit()

def is_admin(uid):
    cur.execute("SELECT uid FROM admins WHERE uid = ?", (uid,))
    return cur.fetchone() is not None

def get_all_admins():
    cur.execute("SELECT uid FROM admins")
    return [row[0] for row in cur.fetchall()]

def log_game(uid, game_name, bet, win_amount, coef):
    conn = sqlite3.connect("lira_ultimate_v2.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO history (uid, game_name, bet, win_amount, coef) VALUES (?, ?, ?, ?, ?)",
                (uid, game_name, bet, win_amount, coef))
    conn.commit()
    conn.close()

def parse_bet(val, user_bal):
    val = str(val).lower().strip().replace("кк", "000000").replace("к", "000")
    if val == "все": return user_bal
    try:
        res = int(val)
        return res if 100 <= res <= user_bal else -1
    except: return -2

def get_link(u):
    return f"[{u[1]}](tg://user?id={u[0]})"

# --- КЛАВИАТУРЫ ---
def main_kb():
    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="👤 Профиль"), types.KeyboardButton(text="🎁 Бонус"))
    kb.row(types.KeyboardButton(text="🏆 Топ игроки"))
    kb.row(types.KeyboardButton(text="📍 Помощь"), types.KeyboardButton(text="➕ Добавить"))
    return kb.as_markup(resize_keyboard=True)

# --- НИКИ И БАЛАНС ---
@dp.message(F.text.lower().startswith("+ник "))
async def set_new_nick(m: types.Message):
    new_nick = m.text[5:].strip().replace("[", "").replace("]", "")
    if len(new_nick) > 20 or len(new_nick) < 2:
        return await m.reply("❌ Ник от 2 до 20 символов!")
    cur.execute("UPDATE users SET name = ? WHERE uid = ?", (new_nick, m.from_user.id))
    conn.commit()
    await m.reply(f"✅ Ваш ник изменен на: {get_link([m.from_user.id, new_nick])}", parse_mode="Markdown")

@dp.message(F.text.lower() == "ник")
async def show_nick(m: types.Message):
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    u = get_u(target.id, target.full_name)
    await m.reply(f"👤 Ник: {get_link(u)}", parse_mode="Markdown")

@dp.message(F.text.lower() == "б")
async def show_my_balance(m: types.Message):
    # Пытаемся получить баланс
    cur.execute("SELECT bal FROM users WHERE uid = ?", (m.from_user.id,))
    res = cur.fetchone()
    
    if res is None:
        # Если пользователя нет, регистрируем его «на лету»
        # Передаем id, имя и юзернейм
        u = get_u(m.from_user.id, m.from_user.full_name, m.from_user.username)
        balance = u[2] # 10000 по умолчанию
    else:
        balance = res[0]

    # Отправляем баланс жирным через HTML
    await m.reply(f"💸 Баланс: <b>{balance:,}</b> лир", parse_mode="HTML")
    
# --- ПЕРЕДАЧА И ВЫДАЧА ---
@dp.message(F.text.lower().startswith("дать "))
async def transfer(m: types.Message):
    if not m.reply_to_message: 
        return await m.reply("❌ Ответьте на сообщение игрока!")
    
    u = get_u(m.from_user.id, m.from_user.full_name)
    t_raw = m.reply_to_message.from_user
    t = get_u(t_raw.id, t_raw.full_name)
    
    if t_raw.is_bot or t[0] == u[0]: 
        return await m.reply("❌ Ошибка!")
    
    # Сумма перевода
    try:
        bet = parse_bet(m.text.split()[1] if len(m.text.split())>1 else "0", u[2])
    except:
        return await m.reply("❌ Введите сумму!")

    if bet < 100: 
        return await m.reply("❌ Минимум 100 лир!")

    # Проверка данных из БД
    cur.execute("SELECT level, used_limit, bal FROM users WHERE uid = ?", (u[0],))
    row = cur.fetchone()
    u_lv, u_used, u_bal = row[0], row[1], row[2]

    if bet > u_bal: 
        return await m.reply("❌ Недостаточно лир на балансе!")

    # Проверка лимита
    u_limit = LEVELS[u_lv]["limit"]
    if (u_used + bet) > u_limit:
        remains = u_limit - u_used
        return await m.reply(
            f"⚠️ **ЛИМИТ ИСЧЕРПАН!**\n"
            f"━━━━━━━━━━━━━━\n"
            f"Ваш уровень (**{u_lv}**) позволяет передать еще **{max(0, remains):,}** лир сегодня.\n\n"
            f"Лимиты обновляются в **22:00 МСК**.",
            parse_mode="Markdown"
        )

    # Проведение транзакции
    upd_bal(u[0], -bet)
    upd_bal(t[0], bet)
    
    # Записываем расход лимита
    cur.execute("UPDATE users SET used_limit = used_limit + ? WHERE uid = ?", (bet, u[0]))
    conn.commit()

    await m.answer(f"✅ {get_link(u)} передал **{bet:,}** лир игроку {get_link(t)}!", parse_mode="Markdown")
    
# --- 1. КОМАНДА ВЫДАТЬ (через реплай) ---
@dp.message(F.text.lower().startswith("выдать "))
async def adm_give_fast(m: types.Message):
    # Проверка доступа для списка админов
    if m.from_user.id not in ADMIN_ID: return 
    
    if not m.reply_to_message: 
        return await m.reply("❌ **Ответьте на сообщение игрока (реплай)!**", parse_mode="Markdown")
    
    try:
        # Поддержка 'к', чтобы можно было писать 'выдать 50к'
        summ_raw = m.text.split()[1].lower().replace("к", "000").replace("k", "000")
        summ = int(summ_raw)
        
        target_id = m.reply_to_message.from_user.id
        target_name = m.reply_to_message.from_user.first_name
        
        upd_bal(target_id, summ)
        
        await m.answer(
            f"👑 **АДМИНИСТРАЦИЯ**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Выдано: **{summ:,}** лир\n"
            f"👤 Игрок: **{target_name}**\n"
            f"━━━━━━━━━━━━━━", 
            parse_mode="Markdown"
        )
    except: 
        await m.reply("❌ **Ошибка!** Введите сумму числом (например: `выдать 10000` или `выдать 10к`)", parse_mode="Markdown")

import random
from aiogram import F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

# --- 1. Твоя формула коэффициентов ---
def get_mines_coef(mines_count: int, opened: int) -> float:
    total = 25
    if mines_count >= total or opened <= 0:
        return 1.0
    safe = total - mines_count
    prob = 1.0
    for i in range(opened):
        prob *= (safe - i) / (total - i)
    coef = (1.0 / prob) * 0.96
    return round(coef, 2)

# --- 2. Команда запуска ---
@dp.message(F.text.lower().startswith("мины"))
async def mines_start(m: types.Message, state: FSMContext):
    u = get_u(m.from_user.id, m.from_user.full_name)
    args = m.text.split()
    
    try:
        bet = parse_bet(args[1], u[2])
        mines_cnt = int(args[2]) if len(args) > 2 else 5
    except:
        return await m.reply("❌ Формат: `мины [ставка] [кол-во мин]`")

    if bet < 100: return await m.reply("❌ Ставка от 100 лир!")
    if not (1 <= mines_cnt <= 24): return await m.reply("❌ Мин может быть от 1 до 24!")
    if u[2] < bet: return await m.reply("❌ Недостаточно средств!")

    # Генерируем поле
    field = [1] * mines_cnt + [0] * (25 - mines_cnt)
    random.shuffle(field)

    # Списываем ставку
    upd_bal(m.from_user.id, -bet)

    data = {
        "bet": bet,
        "mines_cnt": mines_cnt,
        "field": field,
        "opened": 0,
        "opened_indices": [],
        "coef": 1.0,
        "game_id": random.randint(100000, 999999)
    }
    
    await state.update_data(data)
    await mines_render(m, data)

# --- 3. Отрисовка сетки 5х5 ---
async def mines_render(m, d):
    kb = InlineKeyboardBuilder()
    for i in range(25):
        if i in d['opened_indices']:
            txt = "💎"
        else:
            txt = "❓"
        kb.button(text=txt, callback_data=f"mine_step_{i}")
    kb.adjust(5)

    # Автовыбор
    kb.row(types.InlineKeyboardButton(text="🔄 Автовыбор", callback_data="mine_auto"))
    
    # Забрать
    if d['opened'] > 0:
        kb.row(types.InlineKeyboardButton(
            text=f"✅ Забрать выигрыш {d['coef']}X", 
            callback_data="mine_stop"
        ))

    text = (f"✨ **Игра «Мины» #{d['game_id']} продолжается!**\n\n"
            f"💠 **Ставка:** {d['bet']:,} лир\n"
            f"💎 {d['opened']} | 💣 {d['mines_cnt']}\n"
            f"📈 **Текущий множитель:** x{d['coef']}\n\n"
            f"_Следующий клик может быть победным... или последним._")

    if isinstance(m, types.Message):
        await m.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    else:
        await m.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

# --- 4. Логика хода и Автовыбора ---
@dp.callback_query(F.data.startswith("mine_step_"))
@dp.callback_query(F.data == "mine_auto")
async def mine_logic(call: types.CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d: return await call.answer()

    if call.data == "mine_auto":
        available = [i for i in range(25) if i not in d['opened_indices']]
        idx = random.choice(available)
    else:
        idx = int(call.data.split("_")[2])

    if idx in d['opened_indices']: return await call.answer()

    if d['field'][idx] == 1: # Поражение
        final_kb = InlineKeyboardBuilder()
        for i in range(25):
            txt = "💣" if d['field'][i] == 1 else "🔹"
            final_kb.button(text=txt, callback_data="none")
        final_kb.adjust(5)

        await call.message.edit_text(
            f"💣 **МИНОЕ ПОЛЕ — ПРОИГРЫШ**\n\n"
            f"Вы подорвались! Ставка **{d['bet']:,}** лир потеряна.",
            reply_markup=final_kb.as_markup())
        await state.clear()
    else: # Успех
        d['opened'] += 1
        d['opened_indices'].append(idx)
        d['coef'] = get_mines_coef(d['mines_cnt'], d['opened'])
        await state.update_data(d)
        await mines_render(call, d)
    await call.answer()

# --- 5. Завершение игры (Забрать) ---
@dp.callback_query(F.data == "mine_stop")
async def mine_stop(call: types.CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d: return

    # Зачисляем полную ставку с коэффициентом
    win_total = int(d['bet'] * d['coef'])
    upd_bal(call.from_user.id, win_total)

    # Финальное поле
    final_kb = InlineKeyboardBuilder()
    for i in range(25):
        txt = "💣" if d['field'][i] == 1 else "💎"
        final_kb.button(text=txt, callback_data="none")
    final_kb.adjust(5)

    await call.message.edit_text(
        f"💎 **МИНЫ #{d['game_id']} — ИГРА ЗАВЕРШЕНА**\n\n"
        f"💰 **Ставка:** {d['bet']:,} лир\n"
        f"📈 **Коэффициент:** x{d['coef']}\n"
        f"💵 **Выигрыш:** {win_total:,} лир\n"
        f"💣 {d['mines_cnt']} | 💎 {25 - d['mines_cnt']}\n\n"
        f"_Ты прошёл по полю смерти и остался жив._",
        reply_markup=final_kb.as_markup(), parse_mode="Markdown"
    )
    
    await state.clear()
    await call.answer("Выигрыш зачислен!")



import random
import json
import os
from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Константы ---
CARDS_VALUES = {'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13}
CARDS_NAMES = list(CARDS_VALUES.keys())
ACTIVE_GAMES_FILE = "hilo_active_games.json"

def load_active_games():
    if os.path.exists(ACTIVE_GAMES_FILE):
        with open(ACTIVE_GAMES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_active_games(games):
    with open(ACTIVE_GAMES_FILE, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False)

# --- Отрисовка интерфейса ---
async def hl_render_game(m, game, finished=False):
    card = game['last']
    coef = game['coef']
    bet = game['bet']
    val = CARDS_VALUES[card]

    # Коэффициенты
    prob_up = (13 - val + 1) / 13
    prob_down = val / 13
    next_up = max(round((1 / prob_up) * 0.92, 2), 1.1)
    next_down = max(round((1 / prob_down) * 0.92, 2), 1.1)
    k_same = 11.50

    kb = InlineKeyboardBuilder()
    
    if not finished:
        if card == 'K':
            kb.row(
                types.InlineKeyboardButton(text=f"⏺️ Та же [x{round(coef * k_same, 2)}]", callback_data=f"hl_same_{k_same}"),
                types.InlineKeyboardButton(text=f"⬇️ Ниже [x{round(coef * next_down, 2)}]", callback_data=f"hl_down_{next_down}")
            )
        elif card == 'A':
            kb.row(
                types.InlineKeyboardButton(text=f"⬆️ Выше [x{round(coef * next_up, 2)}]", callback_data=f"hl_up_{next_up}"),
                types.InlineKeyboardButton(text=f"⏺️ Та же [x{round(coef * k_same, 2)}]", callback_data=f"hl_same_{k_same}")
            )
        else:
            kb.row(
                types.InlineKeyboardButton(text=f"⬆️ Выше [x{round(coef * next_up, 2)}]", callback_data=f"hl_up_{next_up}"),
                types.InlineKeyboardButton(text=f"⬇️ Ниже [x{round(coef * next_down, 2)}]", callback_data=f"hl_down_{next_down}")
            )
        
        if coef > 1.0:
            kb.row(types.InlineKeyboardButton(text=f"💰 ЗАБРАТЬ {int(bet * coef):,}", callback_data="hl_collect"))

    text = (
        f"🃏 <b>HI-LO</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 Ставка: <b>{bet:,}</b>\n"
        f"📈 Множитель: <b>x{coef}</b>\n"
        f"💰 Выигрыш: <b>{int(bet * coef):,}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎴 Карта: <b>{card}</b>\n"
    )

    if finished:
        if game.get('result') == "win":
            text += f"\n✅ Выигрыш зачислен: <b>{int(bet * coef):,}</b> лир!"
        else:
            text += f"\n❌ Проигрыш! Выпала карта <b>{card}</b>"

    # ИСПРАВЛЕНИЕ: Если игра закончена, reply_markup = None
    markup = kb.as_markup() if not finished else None

    if isinstance(m, types.Message):
        await m.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        try:
            await m.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except:
            pass # Игнорируем ошибки, если сообщение не изменилось

# --- Старт ---
@dp.message(F.text.lower().startswith("хл"))
async def hl_start(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name, m.from_user.username)
    args = m.text.split()
    try:
        bet = parse_bet(args[1] if len(args) > 1 else "0", u[2])
    except: return

    if bet < 100: return await m.reply("❌ Минимум <b>100</b> лир", parse_mode="HTML")
    if u[2] < bet: return await m.reply("❌ Недостаточно лир!", parse_mode="HTML")

    active_games = load_active_games()
    if str(m.from_user.id) in active_games:
        return await m.reply("⚠️ Доиграйте прошлую игру!")

    upd_bal(m.from_user.id, -bet)
    start_card = random.choice(['3', '4', '5', '6', '7', '8', '9', '10', 'J'])
    game = {"bet": bet, "last": start_card, "coef": 1.0, "finished": False}
    active_games[str(m.from_user.id)] = game
    save_active_games(active_games)
    await hl_render_game(m, game)

# --- Кнопки ---
@dp.callback_query(F.data.startswith("hl_"))
async def hl_callback(call: types.CallbackQuery):
    user_id = str(call.from_user.id)
    active_games = load_active_games()
    if user_id not in active_games: return await call.answer("Игра не найдена")

    game = active_games[user_id]
    if game.get('finished'): return await call.answer()

    if call.data == "hl_collect":
        payout = int(game['bet'] * game['coef'])
        upd_bal(call.from_user.id, payout)
        game.update({"finished": True, "result": "win"})
        # Сначала сохраняем, потом удаляем
        await hl_render_game(call, game, finished=True)
        del active_games[user_id]
        save_active_games(active_games)
        return await call.answer("💰 Забрали!")

    _, action, step_k = call.data.split("_")
    new_card = random.choice(CARDS_NAMES)
    old_val = CARDS_VALUES[game['last']]
    new_val = CARDS_VALUES[new_card]

    if new_val == old_val:
        if action == "same":
            game['coef'] = round(game['coef'] * float(step_k), 2)
            await call.answer(f"⏺️ Повтор! x{step_k}")
        else:
            await call.answer(f"🃏 Снова {new_card}! Продолжаем...")
        game['last'] = new_card
        active_games[user_id] = game
        save_active_games(active_games)
        return await hl_render_game(call, game)

    win = False
    if action == "up" and new_val > old_val: win = True
    elif action == "down" and new_val < old_val: win = True

    if win:
        game['coef'] = round(game['coef'] * float(step_k), 2)
        game['last'] = new_card
        active_games[user_id] = game
        save_active_games(active_games)
        await hl_render_game(call, game)
        await call.answer(f"✅ {new_card}")
    else:
        game.update({"finished": True, "result": "lose", "last": new_card})
        await hl_render_game(call, game, finished=True)
        del active_games[user_id]
        save_active_games(active_games)
        await call.answer(f"❌ Выпала {new_card}", show_alert=True)

# --- ЭМОДЗИ ИГРЫ ---
@dp.message(F.text.lower().startswith(("дартс", "футбол", "баскетбол", "боулинг", "спин")))
async def emoji_games(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name); args = m.text.split(); cmd = args[0].lower()
    bet = parse_bet(args[1] if len(args)>1 else "0", u[2])
    if bet < 100: return
    target = args[2].lower() if cmd == "дартс" and len(args)>2 else None
    if cmd == "дартс" and not target: return await m.reply("📖 `дартс [сумма] [б/к/ц/м]`")
    upd_bal(u[0], -bet); emo = {"дартс":"🎯", "футбол":"⚽️", "баскетбол":"🏀", "боулинг":"🎳", "спин":"🎰"}
    msg = await m.answer_dice(emoji=emo[cmd]); val = msg.dice.value; await asyncio.sleep(4)
    win = 0
    if cmd == "дартс":
        res = {1:'м', 2:'б', 3:'к', 4:'б', 5:'к', 6:'ц'}.get(val, 'м')
        if target == res: win = bet * (3 if target in ['ц', 'м'] else 2)
    elif cmd == "футбол" and val >= 3: win = int(bet*1.6)
    elif cmd == "баскетбол" and val >= 4: win = int(bet*1.8)
    elif cmd == "боулинг" and val == 6: win = int(bet*2.2)
    elif cmd == "спин" and val in [1, 22, 43, 64]: win = bet*2
    if win > 0:
        upd_bal(u[0], win); await m.reply(f"✅ Победа! {get_link(u)} +{win:,} лир.", parse_mode="Markdown")
    else: await m.reply(f"❌ Проигрыш! {get_link(u)} -{bet:,} лир.", parse_mode="Markdown")

# --- X50 ---
x50_lobby = {"active": False, "bets": []}

@dp.message(F.text.lower() == "дроп")
async def show_drop(m: types.Message):
    cur.execute("SELECT res FROM x50_history ORDER BY id DESC LIMIT 10")
    h = cur.fetchall()
    txt = "📜 **История X50:**\n" + "\n".join([f"• {x[0]}" for x in h])
    await m.answer(txt)

@dp.message(F.text.lower().startswith("х50"))
async def x50_start(m: types.Message):
    if m.chat.id != X50_CHAT_ID: return await m.reply("❌ Игра Х50 доступна только в официальном чате @lirachatik!")
    args = m.text.split(); u = get_u(m.from_user.id, m.from_user.full_name)
    if len(args) < 3: return await m.reply("📖 `х50 [сумма] [ч/ф/к/з]`")
    bet = parse_bet(args[1], u[2])
    col = args[2].lower()
    cmap = {'ч':('black','⚫',2), 'ф':('purple','🟣',3), 'к':('red','🔴',5), 'з':('green','🟢',50)}
    if col not in cmap or bet <= 0: return await m.reply("❌ Ошибка!")
    upd_bal(u[0], -bet)
    cur.execute("UPDATE users SET last_x50_bet=? WHERE uid=?", (f"{col}:{bet}", u[0]))
    x50_lobby["bets"].append({"uid": u[0], "name": u[1], "bet": bet, "col": cmap[col][0]})
    await m.reply(f"{cmap[col][1]} {u[1]} поставил {bet:,} лир на x{cmap[col][2]}")
    if not x50_lobby["active"]:
        x50_lobby["active"] = True; await asyncio.sleep(15); await run_x50(m.chat.id)

async def run_x50(cid):
    res_k = random.choices(['black','purple','red','green'], weights=[45,35,19,1])[0]
    rmap = {'black':('⚫ x2',2), 'purple':('🟣 x3',3), 'red':('🔴 x5',5), 'green':('🟢 x50',50)}
    cur.execute("INSERT INTO x50_history (res) VALUES (?)", (rmap[res_k][0],)); conn.commit()
    text = f"🎡 Рулетка X50: {rmap[res_k][0]}\n\n"
    for code, name, emoji, mult in [('ч','black','⚫',2), ('ф','purple','🟣',3), ('к','red','🔴',5), ('з','green','🟢',50)]:
        bets = [b for b in x50_lobby["bets"] if b["col"] == name]
        if not bets: continue
        text += f"{emoji} Ставки на x{mult}:\n"
        for b in bets:
            if b["col"] == res_k:
                win = b["bet"]*mult; upd_bal(b["uid"], win)
                text += f"💸 {b['name']} — {b['bet']:,} → {win:,}\n"
            else: text += f"❌ {b['name']} — {b['bet']:,} → 0\n"
        text += "\n" # ПРОБЕЛ МЕЖДУ ЦВЕТАМИ
    await bot.send_message(cid, text, reply_markup=InlineKeyboardBuilder().button(text="🔁 Повторить ставку", callback_data="x50_re").as_markup())
    x50_lobby["active"], x50_lobby["bets"] = False, []



# --- ФЛИП И ОХОТА ---
@dp.message(F.text.lower().startswith("флип"))
async def flip(m: types.Message):
    u = get_u(m.from_user.id); args = m.text.split()
    bet = parse_bet(args[1], u[2]) if len(args)>1 else 0
    if bet <= 0: return await m.reply("📖 `флип [ставка]`")
    kb = InlineKeyboardBuilder().button(text="🦅 Орел", callback_data=f"fl_o_{bet}").button(text="🪙 Решка", callback_data=f"fl_r_{bet}").as_markup()
    await m.answer(f"🪙 Флип на {bet:,}:", reply_markup=kb)

@dp.callback_query(F.data.startswith("fl_"))
async def fl_res(call: types.CallbackQuery):
    _, c, b = call.data.split("_"); b = int(b); res = random.choice(['o','r']); upd_bal(call.from_user.id, -b)
    msg = await call.message.edit_text("🪙 Крутим..."); await asyncio.sleep(2)
    if c == res:
        upd_bal(call.from_user.id, b*2); await msg.edit_text(f"✅ Выпало: {'Орел' if res=='o' else 'Решка'}. +{b*2:,}!")
    else: await msg.edit_text(f"❌ Выпало: {'Орел' if res=='o' else 'Решка'}. -{b:,}")

@dp.message(F.text.lower().startswith("охота"))
async def hunt(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name); bet = parse_bet(m.text.split()[1] if len(m.text.split())>1 else "0", u[2])
    if bet < 100: return
    upd_bal(u[0], -bet); await m.answer("🏹 Охотимся..."); await asyncio.sleep(2)
    if random.random() < 0.4:
        w = int(bet*2.5); upd_bal(u[0], w); await m.answer(f"🎯 Попал! {get_link(u)} +{w:,}", parse_mode="Markdown")
    else: await m.answer(f"💨 Мимо! {get_link(u)} -{bet:,}", parse_mode="Markdown")

# --- ПРОМОКОДЫ ---
@dp.message(F.text.lower().startswith(("промо", "/promo")))
async def promo_act(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name); args = m.text.split()
    if len(args) < 2: return await m.reply("📖 `промо [код]`")
    code = args[1].upper()
    cur.execute("SELECT amount, uses FROM promo WHERE code=?", (code,))
    p = cur.fetchone()
    if not p: return await m.reply("❌ Нет такого промо!")
    cur.execute("SELECT * FROM promo_history WHERE uid=? AND code=?", (u[0], code))
    if cur.fetchone(): return await m.reply("⚠️ Уже активирован!")
    if p[1] <= 0: return await m.reply("❌ Активации закончились!")
    upd_bal(u[0], p[0]); cur.execute("UPDATE promo SET uses=uses-1 WHERE code=?", (code,))
    cur.execute("INSERT INTO promo_history VALUES (?,?)", (u[0], code)); conn.commit()
    await m.answer(f"✅ Активирован! +{p[0]:,} лир.")

# --- АДМИНКА ---
@dp.message(Command("admin"))
async def adm_panel(m: types.Message):
    # Печатаем в консоль, кто нажал (для теста)
    print(f"Команду админ нажал: {m.from_user.id}")
    print(f"Список админов сейчас: {ADMIN_ID}")

    # Новая супер-проверка (работает и со списками, и с числами)
    user_id = m.from_user.id
    
    if user_id not in ADMIN_ID:
        # Если вы нажали, но вас не пустило, вы увидите это сообщение:
        return await m.answer(f"❌ Доступ запрещен. Ваш ID: `{user_id}`", parse_mode="Markdown")

    # Если проверка прошла, показываем меню
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Выдать", callback_data="adm_give")
    kb.button(text="🎟 Промо", callback_data="adm_promo")
    kb.button(text="📢 Рассылка", callback_data="adm_mail")
    kb.adjust(2)
    
    await m.answer(
        "⚙️ **ПАНЕЛЬ УПРАВЛЕНИЯ LIRA**\n"
        "━━━━━━━━━━━━━━\n"
        "Добро пожаловать, администратор!", 
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "adm_promo")
async def adm_p1(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("Название промо:"); await state.set_state(AdminStates.promo_name)

@dp.message(AdminStates.promo_name)
async def adm_p2(m: types.Message, state: FSMContext):
    await state.update_data(p_n=m.text.upper()); await m.answer("Сумма:"); await state.set_state(AdminStates.promo_sum)

@dp.message(AdminStates.promo_sum)
async def adm_p3(m: types.Message, state: FSMContext):
    await state.update_data(p_s=m.text); await m.answer("Активаций:"); await state.set_state(AdminStates.promo_uses)

@dp.message(AdminStates.promo_uses)
async def adm_p4(m: types.Message, state: FSMContext):
    d = await state.get_data(); n, s, u = d['p_n'], int(d['p_s']), int(m.text)
    cur.execute("INSERT INTO promo VALUES (?,?,?)", (n, s, u)); conn.commit()
    await m.answer(f"✅ Создан: {n}"); await state.clear()
    await bot.send_message(X50_CHAT_ID, f"🎁 **НОВЫЙ ПРОМОКОД!**\n\n🎫 Код: `{n}`\n💰 Сумма: {s:,}\n👤 Активаций: {u}", parse_mode="Markdown")

@dp.callback_query(F.data == "adm_mail")
async def adm_m1(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("Текст рассылки:"); await state.set_state(AdminStates.mailing_text)

@dp.message(AdminStates.mailing_text)
async def adm_m2(m: types.Message, state: FSMContext):
    cur.execute("SELECT uid FROM users"); users = cur.fetchall()
    for u in users:
        try: await bot.send_message(u[0], m.text); await asyncio.sleep(0.05)
        except: pass
    await m.answer("✅ Готово!"); await state.clear()


@dp.message((F.text == "👤 Профиль") | (F.text.lower() == "профиль"))
async def profile_handler(m: types.Message):
    # Если реплай — смотрим чужой профиль, иначе свой
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    
    cur.execute("""SELECT uid, name, bal, reg, level, used_limit, bank, reputation, bio, hide_bal, hide_bank 
                   FROM users WHERE uid = ?""", (target.id,))
    u = cur.fetchone()
    
    if not u: 
        return await m.reply("❌ Игрок еще не зарегистрирован в боте.")

    uid, name, bal, reg, lv, used, bank, rep, bio, h_bal, h_bank = u
    
    # Логика скрытия (владелец профиля всегда видит свои цифры, остальные — "Скрыто")
    is_owner = m.from_user.id == uid
    bal_display = f"{bal:,} лир" if (h_bal == 0 or is_owner) else "🔒 Скрыто"
    bank_display = f"{bank:,} лир" if (h_bank == 0 or is_owner) else "🔒 Скрыто"
    
    # Лимиты
    max_l = LEVELS[lv]["limit"]
    remains = max(0, max_l - used)
    limit_val = f"{remains:,}" if lv < 10 else "Безлимит"

    text = (
        f"👤 **ПРОФИЛЬ ИГРОКА**\n\n"
        f"🎭 Ник: **{name}**\n"
        f"🆔 ID: `{uid}`\n"
        f"📝 Описание: {bio}\n\n"
        f"💰 **ФИНАНСЫ**\n"
        f"├ 💰 Баланс: **{bal_display}**\n"
        f"├ 🏦 Банк: **{bank_display}**\n"
        f"├ ⭐ LVL лимита: **{lv}**\n"
        f"├ 💳 Лимит: **{limit_val}** лир\n"
        f"└ 🔒 Кошелёк: {'Закрыт' if h_bal == 1 else 'Открыт'}\n\n"
        f"📈 **ПРОГРЕСС**\n"
        f"└ 🫡 Репутация: **{rep}**\n\n"
        f"📅 Регистрация: {reg}"
    )
    await m.answer(text, parse_mode="Markdown")
    

# Изменить описание
@dp.message(F.text.lower().startswith("+описание "))
async def set_bio(m: types.Message):
    new_bio = m.text[10:].strip()
    if len(new_bio) > 100: return await m.reply("❌ Описание слишком длинное (макс 100 симв.)")
    cur.execute("UPDATE users SET bio = ? WHERE uid = ?", (new_bio, m.from_user.id))
    conn.commit()
    await m.reply("✅ Описание успешно обновлено!")

# Скрыть/Показать баланс или банк
@dp.message(F.text.lower().startswith("скрыть "))
async def hide_info(m: types.Message):
    what = m.text.lower().split()[1]
    col = "hide_bal" if what == "б" else "hide_bank" if what == "банк" else None
    if not col: return
    
    cur.execute(f"UPDATE users SET {col} = 1 WHERE uid = ?", (m.from_user.id,))
    conn.commit()
    await m.reply(f"🔒 Вы скрыли свой {what} в профиле!")

@dp.message(F.text.lower().startswith("открыть ")) # Доп. функция для возврата
async def show_info(m: types.Message):
    what = m.text.lower().split()[1]
    col = "hide_bal" if what == "б" else "hide_bank" if what == "банк" else None
    if not col: return
    
    cur.execute(f"UPDATE users SET {col} = 0 WHERE uid = ?", (m.from_user.id,))
    conn.commit()
    await m.reply(f"🔓 Ваш {what} снова виден всем!")

@dp.message((F.text.lower().startswith("+реп")) | (F.text.lower().startswith("-реп")))
async def change_rep(m: types.Message):
    if not m.reply_to_message: return await m.reply("❌ Ответьте на сообщение игрока!")
    if m.reply_to_message.from_user.id == m.from_user.id: return await m.reply("❌ Нельзя менять репутацию себе!")
    
    try:
        val = int(m.text.split()[1])
        if val < 1 or val > 150: return await m.reply("❌ Сумма репутации должна быть от 1 до 150!")
    except: return await m.reply("❌ Формат: `+реп 50` или `-реп 50`")

    sign = 1 if "+реп" in m.text.lower() else -1
    total_change = val * sign
    
    cur.execute("UPDATE users SET reputation = reputation + ? WHERE uid = ?", (total_change, m.reply_to_message.from_user.id))
    conn.commit()
    
    status = "повысил" if sign > 0 else "понизил"
    await m.answer(f"🫡 Вы {status} репутацию игроку на **{val}**!")

import re

# Вспомогательная функция для обработки сумм (чтобы работало "банк положить все" или "банк положить 1к")
def parse_amount(text, user_bal):
    text = text.lower().replace('к', '000').replace('k', '000').replace(',', '').replace(' ', '')
    if text in ["все", "всё", "all"]:
        return user_bal
    if text.endswith('%'):
        pct = int(text.replace('%', ''))
        return int(user_bal * pct / 100)
    return int(text)

@dp.message(F.text.lower().startswith("банк"))
async def bank_handler(m: types.Message):
    # Получаем данные игрока: uid[0], name[1], balance[2], bank[6] (проверь индекс bank в своем SELECT)
    # Предположим, твоя функция get_u возвращает список, где balance - это индекс 2
    u = get_u(m.from_user.id, m.from_user.full_name)
    uid = u[0]
    user_balance = u[2]
    
    # Получаем актуальный баланс банка напрямую из БД
    cur.execute("SELECT bank FROM users WHERE uid = ?", (uid,))
    user_bank = cur.fetchone()[0]

    args = m.text.split()

    # 1. Просто команда "банк" — показываем баланс
    if len(args) == 1:
        return await m.reply(
            f"🏦 **Ваш банковский счёт**\n\n"
            f"💰 В хранилище: **{user_bank:,}** лир\n\n"
            f"ℹ️ Чтобы положить: `банк положить [сумма]`\n"
            f"ℹ️ Чтобы снять: `банк снять [сумма]`",
            parse_mode="Markdown"
        )

    # Проверяем, что есть действие и сумма
    if len(args) < 3:
        return await m.reply("❌ Используйте: `банк положить/снять [сумма]`")

    action = args[1].lower()
    amount_raw = args[2]

    try:
        # Если кладем — считаем от баланса на руках, если снимаем — от баланса в банке
        limit = user_balance if action == "положить" else user_bank
        amount = parse_amount(amount_raw, limit)
        
        if amount <= 0:
            return await m.reply("❌ Сумма должна быть больше 0!")
    except:
        return await m.reply("❌ Ошибка! Введите сумму числом или напишите 'все'.")

    # 2. Логика "банк положить"
    if action in ["положить", "внести", "депозит"]:
        if user_balance < amount:
            return await m.reply(f"❌ У вас на руках только **{user_balance:,}** лир.")
        
        # Обновляем БД
        upd_bal(uid, -amount) # Снимаем с рук (твоя функция)
        cur.execute("UPDATE users SET bank = bank + ? WHERE uid = ?", (amount, uid))
        conn.commit()
        
        await m.reply(f"✅ Вы успешно положили в банк **{amount:,}** лир.")

    # 3. Логика "банк снять"
    elif action in ["снять", "вывести"]:
        if user_bank < amount:
            return await m.reply(f"❌ В банке недостаточно средств (у вас там **{user_bank:,}** лир).")
        
        # Обновляем БД
        cur.execute("UPDATE users SET bank = bank - ? WHERE uid = ?", (amount, uid))
        upd_bal(uid, amount) # Добавляем на руки
        conn.commit()
        
        await m.reply(f"✅ Вы успешно сняли из банка **{amount:,}** лир.")
    
    else:
        await m.reply("❌ Неизвестная операция. Используйте `положить` или `снять`.")
        
    
@dp.message(F.text.lower().in_(["🏆 топ игроки", "топ"]))
async def top_players_refined(m: types.Message):
    # Получаем топ-10 богатеев
    cur.execute("SELECT name, bal, uid FROM users ORDER BY bal DESC LIMIT 10")
    rows = cur.fetchall()
    
    txt = "🏆 **ТОП-10 БОГАТЕЕВ:**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    for i, r in enumerate(rows, 1):
        # r[2] - uid, r[0] - name, r[1] - balance
        txt += f"{i}. {get_link([r[2], r[0]])} — **{r[1]:,}** лир\n"
        
    await m.answer(txt, parse_mode="Markdown")

@dp.message(F.text.lower().in_(["🎁 бонус", "бонус"]))
async def bonus_cmd(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name)
    now = datetime.now()
    
    # Проверка на КД (24 часа)
    # u[7] — это столбец 'bonus' в твоей таблице users
    if u[7]:
        last_bonus_time = datetime.strptime(u[7], "%Y-%m-%d %H:%M:%S")
        if last_bonus_time + timedelta(hours=24) > now:
            # Считаем, сколько осталось ждать
            remaining = (last_bonus_time + timedelta(hours=24)) - now
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds // 60) % 60
            return await m.reply(f"❌ Вы уже забирали бонус!\nПриходите через **{hours}ч. {minutes}мин.**")

    # Генерируем случайную сумму от 1000 до 5000
    gift = random.randint(1000, 5000)
    
    # Обновляем баланс и время бонуса
    # Мы используем upd_bal для начисления денег
    upd_bal(u[0], gift)
    
    # Записываем время получения бонуса в базу
    cur.execute("UPDATE users SET bonus = ? WHERE uid = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), u[0]))
    conn.commit()
    
    await m.reply(f"🎁 {get_link(u)}, вы получили ежедневный бонус **{gift:,}** лир!", parse_mode="Markdown")

@dp.message(F.text.lower().in_(["📍 помощь", "помощь"]))
async def help_cmd(m: types.Message):
    # Тег <blockquote> открывается в начале и закрывается в самом конце
    help_text = (
        "📍 <b>Помощь</b>\n\n"
        "<blockquote>"
        "<b>🎮 Игры:</b>\n"
        "🎡<b>Х50 [ставка] [исход] ч,ф,к,з</b>\n"
        "💣<b>Мины [ставка] [кол мины]</b>\n"
        "🧮<b>Хл [ставка]</b>\n"
        "🐊<b>Охота [ставка]</b>\n"
        "🪙<b>Флип [ставка]</b>\n"
        "🏀<b>Баскетбол [ставка]</b>\n"
        "⚽️<b>Футбол [ставка]</b>\n"
        "🎳<b>Боулинг [ставка]</b>\n"
        "🎰<b>Спин [ставка]</b>\n"
        "🐸<b>Жаба [ставка]</b>\n"
        "🔫<b>Рулетка [ставка] [исход]</b>\n"
        "🗼<b>Башня [ставка] [кол мины]</b>\n"
        "🏴‍☠️<b>Пират [ставка] [1-2]</b>\n\n"
        "🔑 <b>Ключевые команды:</b>\n"
        "<b>Б</b> — баланс игрока\n"
        "<b>Топ</b> — Топ 10 игроков\n"
        "<b>Дать [сумма]</b> на ответ игрока — передача валюты\n"
        "<b>Помощь</b> — помощь\n"
        "<b>Шар [текст]</b> — шар ответит рандомно\n"
        "<b>Промо [код]</b> — активировать промо\n\n"
        "<b>📞 Контакты</b>\n"
        "🛎️ <b>Новостной Канал</b> — @LiraGameNews\n"
        "💬 <b>Основной Чат</b> — @Lirachatik\n"
        "🧑‍💻 <b>Основатель</b> — @ren1ved"
        "</blockquote>"
    )
    
    await m.answer(help_text, parse_mode="HTML")

@dp.message(F.text == "➕ Добавить")
async def add_bot_to_chat(m: types.Message):
    # Создаем инлайн-кнопку со ссылкой
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text="➕ Добавить в чат", 
        url="https://t.me/LiraGame_Bot?startgroup=0")
    )
    
    # Отправляем сообщение
    await m.answer(
        "🤖 **Добавьте бота в чат!**\n\n"

             "Чтобы начать играть с друзьями, нажмите кнопку ниже и выберите свою группу. "
        "Не забудьте выдать боту права администратора.",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

# --- ЛОГИКА ОВЕРГО (ОБЛЕГЧЕННАЯ ВЕРСИЯ) ---

@dp.message(F.text.lower().startswith("оверго"))
async def game_overgo(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name)
    args = m.text.split()
    
    # Проверка аргументов
    if len(args) < 3:
        return await m.reply("📖 Формат: **Оверго [ставка] [коэф]**\nПример: `Оверго 100 2.0`", parse_mode="Markdown")
    
    bet = parse_bet(args[1], u[2])
    try:
        target_coef = float(args[2].replace(",", "."))
    except:
        return await m.reply("❌ Укажите корректный **коэффициент**!")

    if bet < 100: return await m.reply("❌ Минимальная ставка — **100** лир!")
    if target_coef <= 1.0: return await m.reply("❌ Коэффициент должен быть выше **1.0**!")

    # --- ОБЛЕГЧЕННЫЙ RTP ---
    # Шанс моментального слива (1.0x) теперь всего 3-5%
    if random.random() < 0.04: 
        crash_point = 1.0
    else:
        # Улучшенная формула: теперь чаще выпадают играбельные иксы
        # Мы берем случайное число и "вытягиваем" его в сторону средних значений
        base = random.uniform(0.1, 1.0)
        crash_point = round(0.98 / base, 2)
        
        # Ограничиваем слишком огромные иксы, чтобы не разорить банк бота
        if crash_point > 100: crash_point = round(random.uniform(50, 100), 2)

    # Небольшая пауза для эффекта ожидания
    await asyncio.sleep(0.8)

    if crash_point >= target_coef:
        # ✅ ПОБЕДА
        win_sum = int(bet * target_coef) - bet
        upd_bal(u[0], win_sum)
        
        text = (
            f"🎮 Игра: **ОверГо**\n"
            f"🎢 График: **{crash_point}x**\n\n"
            f"✅ **Победа!**\n"
            f"💰 Вы выиграли: **{int(bet * target_coef):,}** лир"
        )
    else:
        # 💥 ПОРАЖЕНИЕ
        upd_bal(u[0], -bet)
        
        text = (
            f"🎮 Игра: **ОверГо**\n"
            f"🎢 График: **{crash_point}x**\n\n"
            f"💥 **Поражение.**\n"
            f"📉 Вы проиграли: **{bet:,}** лир"
        )

    await m.reply(text, parse_mode="Markdown")
 

# Глобальная переменная для хранения активной викторины
active_vik = {
    "is_active": False,
    "amount": 0,
    "question": "",
    "answer": ""
}

# --- ИГРА ПИРАТ ---
@dp.message(F.text.lower().startswith("пират"))
async def pirate_start(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name)
    args = m.text.split()
    
    bet = parse_bet(args[1] if len(args) > 1 else "0", u[2])
    if bet < 100: return await m.reply("❌ Ставка от **100** лир!")
    
    # Количество сокровищ (по умолчанию 1, если не указано 2)
    treasures = 2 if len(args) > 2 and args[2] == "2" else 1
    coef = 1.44 if treasures == 2 else 2.88
    
    # Списываем ставку
    upd_bal(u[0], -bet)
    
    kb = InlineKeyboardBuilder()
    for i in range(1, 4):
        kb.button(text=f"💀 {i}", callback_data=f"pirate_play_{i}_{treasures}_{bet}")
    kb.button(text="🤖 Авто-выбор", callback_data=f"pirate_play_auto_{treasures}_{bet}")
    kb.adjust(3, 1)
    
    await m.answer(
        f"⚓️ Игра в **Brawl Pirate**!\n"
        f"💰 Ставка: **{bet:,}** лир\n"
        f"🎁 Сокровищ: **{treasures}** (Коэффициент: **x{coef}**)\n"
        f"💀 Выберите 1 из 3 черепов!",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("pirate_play_"))
async def pirate_callback(call: types.CallbackQuery):
    data = call.data.split("_")
    choice = data[2]
    treasures = int(data[3])
    bet = int(data[4])
    
    if choice == "auto":
        choice = random.randint(1, 3)
    else:
        choice = int(choice)
        
    # Логика шанса: если 1 сокровище — шанс 1/3, если 2 — шанс 2/3
    is_win = random.random() < (treasures / 3)
    coef = 1.44 if treasures == 2 else 2.88
    
    if is_win:
        # Зачисляем полную сумму ставки * коэффициент
        win_total = int(bet * coef)
        upd_bal(call.from_user.id, win_total)
        
        text = (f"💎 **Вы нашли сокровище!**\n\n"
                f"🎰 Выбор пал на череп №{choice}\n"
                f"📈 Коэффициент: **x{coef}**\n"
                f"🏆 Выигрыш: **{win_total:,}** лир")
    else:
        text = (f"💀 **Там было пусто...**\n\n"
                f"🎰 Выбор пал на череп №{choice}\n"
                f"📉 Проигрыш: **{bet:,}** лир")
                
    await call.message.edit_text(text, reply_markup=None, parse_mode="Markdown")


# --- ШАГ 1: Админ пишет /vik ---
# --- 2. ВИКТОРИНА (запуск: /vik) ---
@dp.message(Command("vik"))
async def vik_cmd(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMIN_ID: return
    
    await m.answer("💰 **Шаг [1/3]:** Введите сумму вознаграждения (можно с 'к'):")
    await state.set_state(AdminStates.vik_amount)

@dp.message(AdminStates.vik_amount)
async def vik_get_amount(m: types.Message, state: FSMContext):
    summ_text = m.text.lower().replace("к", "000").replace("k", "000")
    if not summ_text.isdigit():
        return await m.reply("❌ **Введите число!**")
    
    await state.update_data(amount=int(summ_text))
    await m.answer("❓ **Шаг [2/3]:** Введите ВОПРОС викторины:")
    await state.set_state(AdminStates.vik_question)

@dp.message(AdminStates.vik_question)
async def vik_get_question(m: types.Message, state: FSMContext):
    await state.update_data(question=m.text)
    await m.answer("📝 **Шаг [3/3]:** Введите ПРАВИЛЬНЫЙ ОТВЕТ:")
    await state.set_state(AdminStates.vik_answer)

@dp.message(AdminStates.vik_answer)
async def vik_get_answer(m: types.Message, state: FSMContext):
    data = await state.get_data()
    
    active_vik["amount"] = data['amount']
    active_vik["question"] = data['question']
    active_vik["answer"] = m.text.lower().strip()
    active_vik["is_active"] = True
    
    await bot.send_message(
        X50_CHAT_ID, 
        f"🎁 **ВИКТОРИНА!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"❓ Вопрос: **{active_vik['question']}**\n\n"
        f"💰 Приз: **{active_vik['amount']:,}** лир\n"
        f"━━━━━━━━━━━━━━\n"
        f"Кто первым напишет правильный ответ?",
        parse_mode="Markdown"
    )
    await m.answer("✅ **Викторина запущена в чате!**")
    await state.clear()

# --- ПРОВЕРКА ОТВЕТА В ЧАТЕ ---
@dp.message(lambda m: active_vik["is_active"] == True)
async def check_vik_answer(m: types.Message):
    # Если сообщение пришло не из игрового чата, игнорируем (необязательно)
    if m.chat.id != X50_CHAT_ID: return
    
    user_text = m.text.lower().strip()
    
    if user_text == active_vik["answer"]:
        # Сразу выключаем викторину, чтобы никто другой не успел ответить
        active_vik["is_active"] = False
        
        u = get_u(m.from_user.id, m.from_user.full_name)
        upd_bal(u[0], active_vik["amount"])
        
        await m.reply(
            f"🎊 **ЕСТЬ ПОБЕДИТЕЛЬ!**\n\n"
            f"👤 {get_link(u)} правильно ответил: `{active_vik['answer']}`\n"
            f"💰 Приз **{active_vik['amount']:,}** лир зачислен!",
            parse_mode="Markdown"
        )

from datetime import datetime

@dp.message(F.text.lower() == "ласт")
async def last_games(m: types.Message):
    uid = m.from_user.id
    conn = sqlite3.connect("lira_ultimate_v2.db")
    cur = conn.cursor()
    
    # Берем последние 10 записей
    cur.execute("SELECT game_name, bet, win_amount, coef, date FROM history WHERE uid = ? ORDER BY id DESC LIMIT 10", (uid,))
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        return await m.reply("🗂 У вас еще нет истории игр.")

    text = "📜 **Ваши последние 10 игр:**\n\n"
    
    for row in rows:
        game_name, bet, win_amount, coef, g_date = row
        
        # Определяем статус: выиграл или проиграл
        if win_amount > 0:
            status = "✅"
            res_sum = f"+{win_amount:,}"
        else:
            status = "❌"
            res_sum = f"-{bet:,}"
        
        # Форматируем дату (убираем секунды для красоты)
        # Если в базе дата строкой: 2023-10-10 12:00:00 -> 10.10 12:00
        try:
            dt = datetime.strptime(g_date, '%Y-%m-%d %H:%M:%S')
            f_date = dt.strftime('%d.%m %H:%M')
        except:
            f_date = g_date

        text += f"{status} {game_name} | x{coef:.2f} | {res_sum} | {f_date}\n"

    await m.answer(text, parse_mode="Markdown")

# --- 3. ФАСТ КОНКУРС (запуск: /fast) ---
@dp.message(Command("fast"))
async def fast_cmd(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMIN_ID: return
    
    await m.answer("💰 Введите сумму для **ФАСТ КОНКУРСА** (например, 50к):")
    await state.set_state(AdminStates.fast_amount)

@dp.message(AdminStates.fast_amount)
async def fast_publish(m: types.Message, state: FSMContext):
    summ_text = m.text.lower().replace("к", "000").replace("k", "000")
    if not summ_text.isdigit():
        return await m.reply("❌ **Введите число!**")
    
    amount = int(summ_text)
    await state.clear()
    
    kb = InlineKeyboardBuilder()
    kb.button(text="💝 ЗАБРАТЬ", callback_data=f"take_fc_{amount}")
    
    await bot.send_message(
        X50_CHAT_ID,
        f"🎁 **ФАСТ КОНКУРС**\n"
        f"━━━━━━━━━━━━━━\n"
        f"УСПЕЙ ПЕРВЫМ НАЖАТЬ НА КНОПКУ!\n\n"
        f"💰 Сумма: **{amount:,}** лир\n"
        f"━━━━━━━━━━━━━━",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await m.answer("✅ **ФК успешно запущен!**")


# --- ОБРАБОТКА КНОПКИ ФК ---
@dp.callback_query(F.data.startswith("take_fc_"))
async def take_fast_contest(call: types.CallbackQuery):
    # 1. Мгновенно отвечаем серверу Telegram, чтобы убрать "часики" (лаги)
    try:
        await call.answer()
    except:
        pass

    # Извлекаем сумму из даты кнопки
    try:
        amount = int(call.data.split("_")[2])
    except:
        return

    # 2. Проверяем, не завершен ли уже этот конкурс
    # (Если в сообщении уже есть текст "ЗАВЕРШЕН", значит кто-то нажал раньше)
    if "ЗАВЕРШЕН" in (call.message.text or call.message.caption or ""):
        return await call.answer("❌ Этот ФК уже забрали!", show_alert=True)

    try:
        # Получаем данные игрока
        u = get_u(call.from_user.id, call.from_user.full_name)
        
        # Начисляем лиры
        upd_bal(u[0], amount)
        
        # 3. Редактируем сообщение СРАЗУ, чтобы никто другой не успел нажать
        await call.message.edit_text(
            f"✅ **ФАСТ КОНКУРС ЗАВЕРШЕН**\n"
            f"━━━━━━━━━━━━━━\n"
            f"👤 Победитель: **{u[1]}**\n"
            f"💰 Сумма: **{amount:,}** лир\n"
            f"━━━━━━━━━━━━━━\n"
            f"Лиры успешно зачислены на баланс!",
            parse_mode="Markdown"
        )
        
        # Дополнительное уведомление победителю
        await call.answer("🎉 Вы успешно забрали приз!", show_alert=True)
        
    except Exception as e:
        # Если возникла ошибка при редактировании (например, кто-то отредактировал на миллисекунду раньше)
        await call.answer("❌ Вы не успели!", show_alert=False)
# --- 4. ПРОВЕРКА ОТВЕТА ВИКТОРИНЫ (в общем чате) ---
@dp.message(lambda m: active_vik.get("is_active") == True)
async def check_vik_answer(m: types.Message):
    if m.chat.id != X50_CHAT_ID: return
    
    user_text = m.text.lower().strip()
    if user_text == active_vik["answer"]:
        active_vik["is_active"] = False # Выключаем, чтобы не было второго победителя
        
        u = get_u(m.from_user.id, m.from_user.full_name)
        upd_bal(u[0], active_vik["amount"])
        
        await m.reply(
            f"🎊 **ЕСТЬ ПОБЕДИТЕЛЬ!**\n"
            f"━━━━━━━━━━━━━━\n"
            f"👤 **{u[1]}** ответил правильно: `{active_vik['answer']}`\n"
            f"💰 Приз **{active_vik['amount']:,}** лир зачислен!\n"
            f"━━━━━━━━━━━━━━",
            parse_mode="Markdown"
        )

@dp.message(F.text.lower().startswith(("шар", "вероятность")))
async def magic_ball(m: types.Message):
    answers = [
        "🔮 Я думаю — Нет",
        "🔮 Мне кажется — Нет",
        "🔮 Думаю — Да",
        "🔮 Знаки говорят — Да",
        "🔮 Вероятность крайне мала",
        "🔮 Скорее всего — Да",
        "🔮 Звезды говорят — Нет",
        "🔮 Определенно — Да"
    ]
    await m.reply(random.choice(answers))

import re
import random
import time
import sqlite3
from aiogram import types, F

# --- КОНФИГУРАЦИЯ ---
RED_NUMS = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
BLACK_NUMS = [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35]

# Разрешенные текстовые типы ставок
VALID_TYPES = {
    'к': 'красное', 'красное': 'красное',
    'ч': 'черное', 'черное': 'черное',
    'з': 'зеро', 'зеро': 'зеро', '0': 'зеро',
    'евен': 'чет', 'чет': 'чет',
    'одд': 'нечет', 'нечет': 'нечет',
    'м': '1-18', 'б': '19-36'
}

roulette_games = {}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def log_roulette_result(num, emoji):
    conn = sqlite3.connect("lira_ultimate_v2.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO roulette_history (number, color_emoji) VALUES (?, ?)", (num, emoji))
    cur.execute("DELETE FROM roulette_history WHERE id NOT IN (SELECT id FROM roulette_history ORDER BY id DESC LIMIT 10)")
    conn.commit()
    conn.close()

# --- КОМАНДА: СТАВКА И ОТМЕНА ---
@dp.message(F.text.lower().startswith("рул"))
async def roulette_handler(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name)
    args = m.text.lower().split()
    cid = m.chat.id

    # Обработка отмены: "рул отмена"
    if len(args) > 1 and args[1] in ["отмена", "cancel"]:
        if cid in roulette_games and u[0] in roulette_games[cid]['players']:
            total_return = sum(b['bet'] for b in roulette_games[cid]['players'][u[0]])
            upd_bal(u[0], total_return)
            del roulette_games[cid]['players'][u[0]]
            return await m.reply(f"принял ✅ {get_link(u)}, ваши ставки аннулированы. Возвращено: **{total_return:,}** лир.", parse_mode="Markdown")
        return await m.reply("❌ У вас нет активных ставок для отмены.")

    if len(args) < 3:
        return await m.reply("🎰 **РУЛЕТКА**\n\n📝 `рул [сумма] [тип]`\n🎨 Типы: `к`, `ч`, `з`, `евен`, `одд`, `м`, `б`\n🔢 Числа: `1,5,10` (через запятую)\n\n❌ `рул отмена` — забрать ставки", parse_mode="Markdown")

    # Валидация типа
    target = args[2]
    is_valid_word = target in VALID_TYPES
    is_valid_numbers = re.fullmatch(r'^(\d{1,2},?)+$', target)

    if not (is_valid_word or is_valid_numbers):
        return await m.reply(f"❌ Тип `{target}` не распознан. Ставка не принята!")

    if is_valid_numbers:
        nums = [int(x) for x in target.split(',') if x]
        if any(n > 36 for n in nums):
            return await m.reply("❌ В рулетке только числа от 0 до 36!")

    # Валидация суммы
    try:
        amount = parse_bet(args[1], u[2])
    except: return

    if amount < 100: return await m.reply("❌ Минимум 100 лир!")
    if u[2] < amount: return await m.reply("❌ Недостаточно лир!")

    # Регистрация
    if cid not in roulette_games:
        roulette_games[cid] = {'players': {}, 'start_time': time.time()}
    
    if u[0] not in roulette_games[cid]['players']:
        roulette_games[cid]['players'][u[0]] = []

    roulette_games[cid]['players'][u[0]].append({'bet': amount, 'target': target})
    upd_bal(u[0], -amount)

    await m.answer(f"✅ {get_link(u)} поставил **{amount:,}** на `{target}`\n🚀 Пиши `го` для запуска!")

# --- КОМАНДА: ЗАПУСК (го) ---
@dp.message(F.text.lower() == "го")
async def roulette_spin(m: types.Message):
    cid = m.chat.id
    if cid not in roulette_games or not roulette_games[cid]['players']:
        return await m.reply("❌ Ставок еще нет!")
    
    game = roulette_games[cid]
    if time.time() - game['start_time'] < 10:
        return await m.reply(f"⏳ Рано! Ждите {int(10 - (time.time() - game['start_time']))} сек.")

    res_num = random.randint(0, 36)
    color = "🟢" if res_num == 0 else "🔴" if res_num in RED_NUMS else "⚫️"
    log_roulette_result(res_num, color)

    header = f"🎰 **ВЫПАЛО: {res_num} {color}**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    report = ""

    for uid, bets in game['players'].items():
        player = get_u(uid)
        win_total = 0
        details = ""
        
        for b in bets:
            t, a = b['target'], b['bet']
            win, mult = False, 2
            
            if t in ['к', 'красное'] and res_num in RED_NUMS: win = True
            elif t in ['ч', 'черное'] and res_num in BLACK_NUMS: win = True
            elif t in ['з', 'зеро', '0'] and res_num == 0: win, mult = True, 36
            elif t in ['евен', 'чет'] and res_num != 0 and res_num % 2 == 0: win = True
            elif t in ['одд', 'нечет'] and res_num % 2 != 0: win = True
            elif t == 'м' and 1 <= res_num <= 18: win = True
            elif t == 'б' and 19 <= res_num <= 36: win = True
            elif t.replace(',', '').isdigit():
                nums = [int(x) for x in t.split(',') if x]
                if res_num in nums: win, mult = True, 36 / len(nums)

            if win:
                w_amt = int(a * mult)
                win_total += w_amt
                details += f"  ✅ `{t}`: +{w_amt:,}\n"
            else:
                details += f"  ❌ `{t}`: -{a:,}\n"
        
        if win_total > 0:
            upd_bal(uid, win_total)
        report += f"👤 {get_link(player)}:\n{details}"

    del roulette_games[cid]
    await m.answer(header + report, parse_mode="Markdown")

# --- КОМАНДА: ЛОГ (лог) ---
@dp.message(F.text.lower() == "лог")
async def roulette_log(m: types.Message):
    conn = sqlite3.connect("lira_ultimate_v2.db")
    cur = conn.cursor()
    cur.execute("SELECT number, color_emoji FROM roulette_history ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall()
    conn.close()
    
    if not rows: return await m.reply("История пуста")
    
    history = " • ".join([f"{r[0]}{r[1]}" for r in rows])
    await m.answer(f"📃 **ИСТОРИЯ ВЫПАДЕНИЙ:**\n\n{history}", parse_mode="Markdown")

# --- СИСТЕМА КАЗНЫ ---

def get_treasury():
    cur.execute("SELECT balance, reward_per_user FROM treasury WHERE id = 1")
    return cur.fetchone()

@dp.message(F.text.lower() == "казна")
async def show_treasury(m: types.Message):
    res = get_treasury()
    await m.answer(f"🏛 **СОСТОЯНИЕ КАЗНЫ**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                   f"💰 Баланс: **{res[0]:,}** лир\n"
                   f"🎁 Награда за 1 чел: **{res[1]:,}** лир", parse_mode="Markdown")

@dp.message(F.text.lower().startswith("пополнить казну "))
async def fill_treasury(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        amount = int(m.text.split()[2].lower().replace("к", "000").replace("кк", "000000"))
        cur.execute("UPDATE treasury SET balance = balance + ? WHERE id = 1", (amount,))
        conn.commit()
        await m.answer(f"✅ Казна пополнена на **{amount:,}** лир!")
    except:
        await m.answer("❌ Формат: `пополнить казну [сумма]`")

@dp.message(F.text.lower().startswith("изменить приз "))
async def change_reward(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        amount = int(m.text.split()[2].lower().replace("к", "000").replace("кк", "000000"))
        cur.execute("UPDATE treasury SET reward_per_user = ? WHERE id = 1", (amount,))
        conn.commit()
        await m.answer(f"⚙️ Награда за приглашение изменена на **{amount:,}** лир!")
    except:
        await m.answer("❌ Формат: `изменить приз [сумма]`")

# --- ОБРАБОТКА ДОБАВЛЕНИЯ ПОЛЬЗОВАТЕЛЕЙ ---

@dp.message(F.new_chat_members)
async def on_user_added(m: types.Message):
    inviter = m.from_user  # Тот, кто добавил
    new_users = m.new_chat_members  # Список тех, кого добавили
    
    res = get_treasury()
    balance, reward = res[0], res[1]
    
    # Считаем общее вознаграждение
    total_reward = reward * len(new_users)
    
    if balance < total_reward:
        return await m.answer("🏛 В казне недостаточно средств для выплаты вознаграждения.")
    
    # Начисляем пригласившему
    upd_bal(inviter.id, total_reward)
    
    # Списываем из казны
    cur.execute("UPDATE treasury SET balance = balance - ? WHERE id = 1", (total_reward,))
    conn.commit()
    
    # Получаем ники новых участников
    new_names = ", ".join([u.first_name for u in new_users])
    u_inv = get_u(inviter.id, inviter.full_name)
    
    new_balance = balance - total_reward
    
    text = (f"👤 {get_link(u_inv)} добавил **{new_names}**\n"
            f"💰 Вам из казны зачисляем **{total_reward:,}** лир.\n"
            f"🏛 Остаток казны — **{new_balance:,}** лир")
    
    await m.answer(text, parse_mode="Markdown")

import asyncio
import random
from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОМАНДА ЗАПУСКА КУБОВ ---
@dp.message(F.text.lower().startswith("кубы"))
async def cubes_start(m: types.Message):
    u = get_u(m.from_user.id, m.from_user.full_name)
    args = m.text.split()
    
    # Парсим ставку
    try:
        bet = parse_bet(args[1] if len(args) > 1 else "0", u[2])
    except:
        return await m.reply("❌ Формат: `кубы [ставка]` (ответом на сообщение игрока)")

    if not m.reply_to_message:
        return await m.reply("❌ Нужно ответить на сообщение игрока, которого зовете на дуэль!")
    
    target_user = m.reply_to_message.from_user
    if target_user.id == m.from_user.id:
        return await m.reply("❌ Нельзя играть с самим собой!")
    
    if u[2] < bet:
        return await m.reply("❌ У вас недостаточно лир!")

    # Кнопки принятия/отклонения
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Принять", callback_data=f"cube_acc_{m.from_user.id}_{target_user.id}_{bet}")
    kb.button(text="❌ Отклонить", callback_data=f"cube_dec_{m.from_user.id}_{target_user.id}")
    
    await m.answer(
        f"🎲 {get_link(u)} вызывает на кубы {get_link(get_u(target_user.id, target_user.full_name))}\n"
        f"💰 Ставка: **{bet:,}** лир",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

# --- ОБРАБОТКА КНОПОК ---
@dp.callback_query(F.data.startswith("cube_"))
async def cubes_callback(call: types.CallbackQuery):
    data = call.data.split("_")
    action = data[1]
    creator_id = int(data[2])
    target_id = int(data[3])
    
    # 1. Защита "Не вами предназначено"
    if call.from_user.id not in [creator_id, target_id]:
        return await call.answer("❌ Эта игра не предназначена для вас!", show_alert=True)

    # 2. ОТКЛОНЕНИЕ / ОТМЕНА
    if action == "dec":
        if call.from_user.id == target_id: # Отклонил оппонент
            await call.message.edit_text("❌ Дуэль отклонена оппонентом.")
        else: # Отменил создатель
            await call.message.edit_text("❌ Создатель отменил вызов.")
        return

    # 3. ПРИНЯТИЕ
    if action == "acc":
        if call.from_user.id != target_id:
            return await call.answer("❌ Только оппонент может принять вызов!", show_alert=True)
        
        bet = int(data[4])
        creator = get_u(creator_id)
        target = get_u(target_id)

        # Проверка балансов еще раз
        if creator[2] < bet or target[2] < bet:
            return await call.message.edit_text("❌ У одного из игроков не хватает лир.")

        # Списываем ставки
        upd_bal(creator[0], -bet)
        upd_bal(target[0], -bet)

        # Анимация начала
        await call.message.edit_text("🎲 Определяем, кто первый бросает кубы...")
        await asyncio.sleep(3)

        # Бросок первого игрока
        players = [creator, target]
        random.shuffle(players)
        p1, p2 = players[0], players[1]

        await call.message.edit_text(f"🎲 Кидает {get_link(p1)}...")
        msg_dice1 = await call.message.answer_dice("🎲")
        val1 = msg_dice1.dice.value
        await asyncio.sleep(3)

        await call.message.answer(f"🎲 А теперь {get_link(p2)}...")
        msg_dice2 = await call.message.answer_dice("🎲")
        val2 = msg_dice2.dice.value
        await asyncio.sleep(3)

        # Результаты
        res_text = (
            f"📊 **Результат:**\n"
            f"👤 {p1[1]}: {val1}\n"
            f"👤 {p2[1]}: {val2}\n\n"
        )

        if val1 == val2:
            # Ничья - возврат
            upd_bal(p1[0], bet)
            upd_bal(p2[0], bet)
            res_text += "🤝 **Ничья!** Ставки возвращены."
        else:
            winner = p1 if val1 > val2 else p2
            win_sum = int(bet * 1.9) # 1.9x (10% комиссия)
            upd_bal(winner[0], win_sum)
            res_text += f"🏆 Итоги\nПобедитель: **{winner[1]}**\n💰 Выигрыш: **{win_sum:,}** лир"
            
            # Логируем в историю (если она у вас есть)
            log_game(winner[0], "Кубы", bet, win_sum, 1.9)

        await call.message.answer(res_text, parse_mode="Markdown")

import random
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton

# --- СОСТОЯНИЯ ---
class TowerStates(StatesGroup):
    playing = State()

# --- КОЭФФИЦИЕНТЫ (для 1, 2, 3 и 4 мин) ---
TOWER_COEFFS = {
    1: [1.19, 1.42, 1.86, 2.32, 2.9, 3.52],
    2: [1.58, 2.64, 4.4, 7.33, 10.5, 15.0],
    3: [2.38, 5.94, 10.5, 27.11, 72.0, 131.0],
    4: [4.75, 13.0, 58.0, 150.0, 280.0, 500.0]
}

# --- ФУНКЦИЯ ГЕНЕРАЦИИ ПОЛЯ ---
def get_tower_kb(current_row, mines_count, game_data, game_over=False):
    kb = InlineKeyboardBuilder()
    coeffs = TOWER_COEFFS[mines_count]
    
    # Строим башню сверху вниз (с 5 ряда до 0)
    for row_idx in range(5, -1, -1):
        row_buttons = []
        for col_idx in range(5):
            # Если игра окончена и тут была мина
            if game_over and game_data['mines_pos'].get(row_idx) == col_idx:
                row_buttons.append(InlineKeyboardButton(text="💣", callback_data="ignore"))
            # Если ячейка уже успешно открыта игроком
            elif row_idx < current_row and game_data['history'].get(row_idx) == col_idx:
                row_buttons.append(InlineKeyboardButton(text="📦", callback_data="ignore"))
            # Если это текущий активный ряд
            elif row_idx == current_row and not game_over:
                row_buttons.append(InlineKeyboardButton(text="☁️", callback_data=f"twstep_{row_idx}_{col_idx}"))
            # Остальные закрытые ячейки
            else:
                row_buttons.append(InlineKeyboardButton(text="☁️", callback_data="ignore"))
        
        # Добавляем коэффициент ряда слева
        kb.row(InlineKeyboardButton(text=f"x{coeffs[row_idx]}", callback_data="ignore"), *row_buttons)

    # Кнопка "Забрать", если пройден хотя бы один ряд
    if not game_over and current_row > 0:
        current_win = int(game_data['bet'] * coeffs[current_row-1])
        kb.row(InlineKeyboardButton(text=f"💰 ЗАБРАТЬ {current_win:,}", callback_data="tw_collect"))
    
    return kb.as_markup()

# --- КОМАНДА СТАРТА: башня [ставка] [мины] ---
@dp.message(F.text.lower().startswith("башня"))
async def tower_cmd(m: types.Message, state: FSMContext):
    u = get_u(m.from_user.id, m.from_user.full_name)
    args = m.text.split()
    
    try:
        bet = parse_bet(args[1] if len(args) > 1 else "0", u[2])
        # Если мины не указаны, ставим 1. Лимит 1-4.
        mines = int(args[2]) if len(args) > 2 else 1
    except: return

    if not (1 <= mines <= 4):
        return await m.reply("❌ Количество мин должно быть от 1 до 4!")
    if bet < 100: return await m.reply("❌ Минимальная ставка 100!")
    if u[2] < bet: return await m.reply("❌ Недостаточно лир!")

    # Списываем ставку
    upd_bal(u[0], -bet)
    
    # Генерируем одну мину на каждый из 6 рядов
    mines_pos = {i: random.randint(0, 4) for i in range(6)}
    
    data = {
        'bet': bet, 
        'mines_count': mines, 
        'current_row': 0,
        'mines_pos': mines_pos, 
        'history': {}, 
        'user_id': m.from_user.id
    }
    
    await state.set_state(TowerStates.playing)
    await state.update_data(**data)

    await m.answer(
        f"🗼 **ИГРА: БАШНЯ**\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"👤 Игрок: {get_link(u)}\n"
        f"💵 Ставка: **{bet:,}**\n"
        f"💣 Мин в ряду: **{mines}**\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"👆 Выбирай облако в нижнем ряду!",
        reply_markup=get_tower_kb(0, mines, data),
        parse_mode="Markdown"
    )

# --- ОБРАБОТКА ИГРОВОГО ПРОЦЕССА ---
@dp.callback_query(F.data.startswith("tw"), TowerStates.playing)
async def tower_callback(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if call.from_user.id != data['user_id']:
        return await call.answer("❌ Это не ваша игра!", show_alert=True)

    u = get_u(call.from_user.id)
    
    # Кнопка забрать деньги
    if call.data == "tw_collect":
        coeffs = TOWER_COEFFS[data['mines_count']]
        win = int(data['bet'] * coeffs[data['current_row'] - 1])
        upd_bal(u[0], win)
        await call.message.edit_text(
            f"✅ {get_link(u)} успешно покинул башню!\n💰 Выигрыш: **{win:,}** лир.",
            reply_markup=None, parse_mode="Markdown"
        )
        await state.clear()
        return

    # Если нажали на облако (ход)
    if call.data.startswith("twstep_"):
        _, row, col = call.data.split("_")
        row, col = int(row), int(col)
        
        # Проверка на мину
        if data['mines_pos'][row] == col:
            # ПРОИГРЫШ
            await call.message.edit_text(
                f"💥 **БУМ!** {get_link(u)} наступил на мину.\n📉 Ставка **{data['bet']:,}** сгорела.",
                reply_markup=get_tower_kb(row, data['mines_count'], data, game_over=True),
                parse_mode="Markdown"
            )
            await state.clear()
        else:
            # УСПЕШНЫЙ ШАГ
            data['history'][row] = col
            data['current_row'] += 1
            
            # Если дошел до самого верха (6 ряд)
            if data['current_row'] == 6:
                win = int(data['bet'] * TOWER_COEFFS[data['mines_count']][5])
                upd_bal(u[0], win)
                await call.message.edit_text(
                    f"🏆 **ПОБЕДА!** {get_link(u)} прошел всю башню!\n💰 Приз: **{win:,}** лир.",
                    reply_markup=None, parse_mode="Markdown"
                )
                await state.clear()
            else:
                # Продолжаем игру
                await state.update_data(current_row=data['current_row'], history=data['history'])
                await call.message.edit_reply_markup(
                    reply_markup=get_tower_kb(data['current_row'], data['mines_count'], data)
                )
    
    await call.answer()

# --- КОМАНДЫ СНЯТИЯ БАЛАНСА (ТОЛЬКО ДЛЯ АДМИНА) ---

# 1. Снятие через ответ на сообщение (Реплай)
@dp.message(F.text.lower().startswith("снять "))
async def adm_remove_reply(m: types.Message):
    # 1. Проверка доступа для списка админов
    if m.from_user.id not in ADMIN_ID: 
        return

    # 2. Проверка на реплай
    if not m.reply_to_message:
        return await m.reply("❌ **Ответьте на сообщение игрока, у которого нужно снять лиры!**", parse_mode="Markdown")
    
    try:
        args = m.text.split()
        if len(args) < 2:
            return await m.reply("❌ **Введите сумму или слово 'все'**\nПример: `снять 50к` или `снять все`", parse_mode="Markdown")

        target_uid = m.reply_to_message.from_user.id
        target_name = m.reply_to_message.from_user.full_name
        
        # Получаем данные игрока (u[2] — это баланс)
        u = get_u(target_uid, target_name)
        current_balance = u[2]

        # 3. Обработка суммы
        input_val = args[1].lower()
        if input_val == "все" or input_val == "всё":
            amount = current_balance
        else:
            # Поддержка к, кк, k, kk
            summ_raw = input_val.replace("кк", "000000").replace("kk", "000000").replace("к", "000").replace("k", "000")
            amount = int(summ_raw)

        # 4. Проверки баланса
        if amount <= 0:
            return await m.reply("❌ **Сумма должна быть больше 0!**")
        
        if amount > current_balance:
            amount = current_balance # Забираем всё, что есть, если просят больше
            
        # 5. Списание (передаем отрицательное число в вашу функцию)
        upd_bal(target_uid, -amount)
        
        await m.answer(
            f"📉 **ИЗЪЯТИЕ СРЕДСТВ**\n"
            f"━━━━━━━━━━━━━━\n"
            f"👤 Игрок: **{u[1]}**\n"
            f"💰 Списано: **{amount:,}** лир\n"
            f"━━━━━━━━━━━━━━\n"
            f"Действие выполнил администратор.", 
            parse_mode="Markdown"
        )
        
    except ValueError:
        await m.reply("❌ **Ошибка!** Введите корректную сумму (например: `снять 10к`).", parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка в команде снять: {e}")
        await m.reply("❌ **Произошла ошибка при выполнении команды.**")
        
# 2. Снятие по ID игрока
@dp.message(F.text.lower().startswith("обнулить "))
async def adm_remove_id(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    
    try:
        args = m.text.split() # обнулить [id] [сумма]
        target_id = int(args[1])
        u = get_u(target_id)
        
        if args[2].lower() == "все":
            amount = u[2]
        else:
            amount = int(args[2].lower().replace("к", "000").replace("кк", "000000"))
            
        upd_bal(target_id, -amount)
        await m.answer(f"📉 С баланса игрока `{target_id}` снято **{amount:,}** лир!", parse_mode="Markdown")
    except:
        await m.reply("❌ Формат: `обнулить [ID] [сумма/все]`")

import string
import random
import os
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import FSInputFile

# Функция генерации кода
def generate_random_code(length=12):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

async def auto_create_promo():
    promo_name = generate_random_code().lower() # Код как на скрине
    promo_sum = random.randint(2000, 6000)      # Случайная сумма
    promo_uses = random.randint(15, 30)         # Случайные активации
    
    # Сохраняем в вашу таблицу promo
    cur.execute("INSERT INTO promo (name, sum, uses) VALUES (?, ?, ?)", 
                (promo_name, promo_sum, promo_uses))
    conn.commit()

    try:
        # 1. Открываем фон (файл должен быть в папке с ботом)
        img = Image.open("promo_bg.png") 
        draw = ImageDraw.Draw(img)
        
        # 2. Загружаем шрифт (размер подберите под картинку)
        # Если запускаете на Windows, arial.ttf обычно доступен
        font_code = ImageFont.truetype("arial.ttf", 55) # Для промокода
        font_data = ImageFont.truetype("arial.ttf", 35) # Для суммы и юзов

        # 3. Рисуем текст (координаты X и Y нужно подправить под ваш шаблон!)
        # Рисуем промокод в центре синей рамки
        draw.text((280, 245), promo_name, font=font_code, fill="#00d2ff")
        
        # Рисуем сумму (желтым)
        draw.text((230, 360), str(promo_sum), font=font_data, fill="#ffcc00")
        
        # Рисуем количество активаций (зеленым)
        draw.text((545, 360), str(promo_uses), font=font_data, fill="#00ff42")

        # 4. Сохраняем готовую картинку
        path = "current_promo.png"
        img.save(path)

        # 5. Отправляем в основной чат
        await bot.send_photo(
            chat_id=X50_CHAT_ID,
            photo=FSInputFile(path),
            caption="#промо #lira"
        )
        
    except Exception as e:
        print(f"Ошибка при создании фото промо: {e}")
        # Если картинка не удалась, отправляем текстом, чтобы промо не пропал
        await bot.send_message(X50_CHAT_ID, f"🎁 **НОВЫЙ ПРОМОКОД!**\n\n🎫 Код: `{promo_name}`\n💰 Сумма: {promo_sum}\n👤 Юзов: {promo_uses}")

import warnings
# Игнорируем предупреждение об устаревании pkg_resources
warnings.filterwarnings("ignore", category=UserWarning, module='apscheduler')

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Инициализация планировщика с вашим часовым поясом (например, Астана/Алматы)
scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Almaty"))

async def auto_create_promo():
    promo_name = generate_random_code().lower()
    promo_sum = random.randint(2000, 6000)
    promo_uses = random.randint(15, 30)
    
    cur.execute("INSERT INTO promo (name, sum, uses) VALUES (?, ?, ?)", 
                (promo_name, promo_sum, promo_uses))
    conn.commit()

    try:
        # Убедитесь, что файлы promo_bg.png и arial.ttf лежат в папке с ботом!
        img = Image.open("promo_bg.png") 
        draw = ImageDraw.Draw(img)
        font_code = ImageFont.truetype("arial.ttf", 55)
        font_data = ImageFont.truetype("arial.ttf", 35)

        # Рисуем данные (подправьте координаты под ваш фон)
        draw.text((280, 245), promo_name, font=font_code, fill="#00d2ff") # Код
        draw.text((230, 360), str(promo_sum), font=font_data, fill="#ffcc00") # Сумма
        draw.text((545, 360), str(promo_uses), font=font_data, fill="#00ff42") # Юзы

        path = "current_promo.png"
        img.save(path)

        await bot.send_photo(
            chat_id=X50_CHAT_ID,
            photo=FSInputFile(path),
            caption="#промо #lira"
        )
    except Exception as e:
        # Если картинка не создалась (например, нет файла), шлем текст:
        await bot.send_message(X50_CHAT_ID, f"🎁 **НОВЫЙ ПРОМОКОД!**\n\n🎫 Код: `{promo_name}`\n💰 Сумма: {promo_sum}\n👤 Юзов: {promo_uses}")
        print(f"Ошибка промо: {e}")

# Добавляем задачу: каждый час в 00 минут
scheduler.add_job(auto_create_promo, "cron", minute=0)


from datetime import datetime
import pytz

@dp.message(F.text.lower() == "время")
async def show_city_time(m: types.Message):
    # Определяем часовые пояса
    zones = {
        "Киев": "Europe/Kyiv",
        "Москва": "Europe/Moscow",
        "Омск": "Asia/Omsk",
        "Китай": "Asia/Shanghai",
        "Астана": "Asia/Almaty"
    }
    
    text = "•-• **Текущее время в:**\n\n"
    
    for city, zone in zones.items():
        now = datetime.now(pytz.timezone(zone))
        fmt_time = now.strftime("%d.%m.%Y %H:%M:%S")
        text += f"{city} — {fmt_time}\n"
        
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text.lower().startswith("+админ"))
async def add_admin_db(m: types.Message):
    # Только главный владелец может добавлять других (замените ID на свой)
    if m.from_user.id != 8049948727: 
        return await m.reply("❌ **Только главный владелец может назначать админов!**", parse_mode="Markdown")

    new_id = None
    if m.reply_to_message:
        new_id = m.reply_to_message.from_user.id
    elif len(m.text.split()) > 1 and m.text.split()[1].isdigit():
        new_id = int(m.text.split()[1])

    if new_id:
        cur.execute("INSERT OR IGNORE INTO admins VALUES (?)", (new_id,))
        conn.commit()
        await m.answer(f"✅ **Пользователь** `{new_id}` **теперь администратор!**", parse_mode="Markdown")
    else:
        await m.reply("📖 **Используйте:** `+админ [ID]` или ответом на сообщение.")

@dp.message(F.text.lower().startswith("-админ"))
async def del_admin_db(m: types.Message):
    if m.from_user.id != 8049948727: return
    
    target_id = None
    if m.reply_to_message:
        target_id = m.reply_to_message.from_user.id
    elif len(m.text.split()) > 1 and m.text.split()[1].isdigit():
        target_id = int(m.text.split()[1])

    if target_id == 8049948727:
        return await m.reply("❌ **Нельзя снять права с главного владельца!**")

    if target_id:
        cur.execute("DELETE FROM admins WHERE uid = ?", (target_id,))
        conn.commit()
        await m.answer(f"🗑 **Пользователь** `{target_id}` **лишен прав администратора.**", parse_mode="Markdown")

@dp.message(Command("admin"))
async def admin_panel(m: types.Message):
    if is_admin(m.from_user.id):
        await m.answer("🔧 **Админ-панель Lira:**", reply_markup=admin_inline(), parse_mode="Markdown")
    else:
        await m.answer("❌ **Доступ запрещен.**")

@dp.message(F.text.lower() == "куровень")
async def buy_level_request(m: types.Message):
    cur.execute("SELECT level FROM users WHERE uid = ?", (m.from_user.id,))
    res = cur.fetchone()
    u_lv = res[0] if res else 1
    
    if u_lv >= 10:
        return await m.reply("⭐ У вас максимальный уровень!")

    next_lv = u_lv + 1
    price = LEVELS[next_lv]["price"]
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Купить", callback_data=f"buy_lv_up_{next_lv}")
    kb.button(text="❌ Отмена", callback_data="buy_lv_stop")
    
    await m.answer(
        f"⬆️ **ПОВЫШЕНИЕ УРОВНЯ**\n"
        f"━━━━━━━━━━━━━━\n"
        f"Желаете купить **{next_lv} уровень**?\n"
        f"💰 Цена: **{price:,}** лир\n"
        f"📊 Новый лимит: **{LEVELS[next_lv]['limit']:,}**\n"
        f"━━━━━━━━━━━━━━",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("buy_lv_"))
async def buy_level_callback(call: types.CallbackQuery):
    if call.data == "buy_lv_stop":
        return await call.message.edit_text("❌ Покупка отменена.")
    
    next_lv = int(call.data.split("_")[3])
    price = LEVELS[next_lv]["price"]
    
    cur.execute("SELECT bal FROM users WHERE uid = ?", (call.from_user.id,))
    user_bal = cur.fetchone()[0]
    
    if user_bal < price:
        return await call.answer(f"❌ Недостаточно лир! Нужно {price:,}", show_alert=True)
    
    # Списываем баланс и обновляем уровень
    upd_bal(call.from_user.id, -price)
    cur.execute("UPDATE users SET level = ?, used_limit = 0 WHERE uid = ?", (next_lv, call.from_user.id))
    conn.commit()
    
    await call.message.edit_text(f"✅ **Уровень {next_lv} успешно куплен!**\nСуточный лимит повышен.", parse_mode="Markdown")
    

import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройки уровней
LEVELS = {
    1: {"limit": 75000, "price": 0},
    2: {"limit": 125000, "price": 150000},
    3: {"limit": 200000, "price": 250000},
    4: {"limit": 300000, "price": 400000},
    5: {"limit": 400000, "price": 500000},
    6: {"limit": 500000, "price": 750000},
    7: {"limit": 750000, "price": 1000000},
    8: {"limit": 1000000, "price": 1250000},
    9: {"limit": 10000000, "price": 20000000},
    10: {"limit": 999999999999, "price": 35000000} # Безлимит
}

# Вставь это в init_db, чтобы бот не выдавал ошибку "no such column: level"
try:
    cur.execute("ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1")
    conn.commit()
except:
    pass

@dp.message(F.text.lower() == "уровень")
async def show_level(m: types.Message):
    cur.execute("SELECT level, used_limit FROM users WHERE uid = ?", (m.from_user.id,))
    res = cur.fetchone()
    
    u_lv = res[0] if res else 1
    u_used = res[1] if res else 0
    
    max_l = LEVELS[u_lv]["limit"]
    remains = max_l - u_used
    if remains < 0: remains = 0
    
    l_text = f"{max_l:,}" if u_lv < 10 else "Безлимит"
    
    await m.answer(
        f"📊 **ВАШ СТАТУС**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⭐ Уровень: **{u_lv}**\n"
        f"💰 Суточный лимит: **{l_text}**\n"
        f"📉 Осталось на сегодня: **{remains:,}**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔄 Обнуление лимитов в **22:00 МСК**\n"
        f"🛒 Повысить лимит: `куровень`",
        parse_mode="Markdown"
    )

async def reset_daily_limits():
    cur.execute("UPDATE users SET used_limit = 0")
    conn.commit()
    print("Лог: Суточные лимиты всех игроков обнулены (22:00 МСК).")

# Настройка планировщика
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
# Ставим задачу на 22:00 каждый день
scheduler.add_job(reset_daily_limits, 'cron', hour=22, minute=0)
scheduler.start()

def get_u(uid, name, username=None):
    cur.execute("SELECT * FROM users WHERE uid = ?", (uid,))
    res = cur.fetchone()
    if not res:
        reg_date = datetime.now().strftime("%d.%m.%Y")
        # Сохраняем имя и юзернейм (очищенный от @)
        uname = username.replace("@", "") if username else None
        cur.execute("INSERT INTO users (uid, name, reg, level, used_limit, username) VALUES (?, ?, ?, ?, ?, ?)", 
                    (uid, name, reg_date, 1, 0, uname))
        conn.commit()
        return get_u(uid, name, username)
    return res

@dp.message(Command("start"))
async def start(m: types.Message):
    get_u(m.from_user.id, m.from_user.full_name)
    await m.answer("🎰 Добро пожаловать в Lira! Заходите в основной чат:@lirachatik", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
