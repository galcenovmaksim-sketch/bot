import asyncio
import logging
import json
import time
import re
from datetime import datetime, timedelta

# Aiogram
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# Tools
import aiohttp
import aiosqlite
import google.generativeai as genai

# =========================================================
# ⚙️ КОНФИГУРАЦИЯ (ВАШИ КЛЮЧИ)
# =========================================================
BOT_TOKEN = "8570491805:AAGXnkG2hv9o2SstxhdcTA26qkP53cm-ikU"
GEMINI_KEY = "AIzaSyDJ_xh5D37AoSTGrq5kma0n-DI5yVNUx54"

SHIKI_URL = "https://shikimori.one"
SHIKI_API = "https://shikimori.one/api"

# 🌍 БАЗА ИСТОЧНИКОВ (Самый полный список)
WATCH_SITES = {
    "⭐ AnimeGO": "https://animego.org/search?q=",
    "🟢 Jut.su": "https://jut.su/search/?q=",
    "📺 AniBar": "https://anibar.one/search?q=",
    "🦊 Yummy": "https://yummyanime.tv/search?word=",
    "🎙 AniLibria": "https://anilibria.tv/search?q=",
    "🟣 FindAnime": "https://findanime.net/search?q=",
    "🎞 SovetRom": "https://sovetromantica.com/anime?query=",
    "🔴 YouTube": "https://www.youtube.com/results?search_query="
}

# Настройка AI
ai_model = None
if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        ai_model = genai.GenerativeModel('gemini-1.5-flash')
    except:
        logging.warning("AI Disabled")

# =========================================================
# 🗄️ БАЗА ДАННЫХ + КЕШИРОВАНИЕ (ДЛЯ СКОРОСТИ)
# =========================================================
async def init_db():
    async with aiosqlite.connect('anime_ultra.db') as db:
        # Основные таблицы
        await db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER, anime_id INTEGER, title TEXT, 
                score REAL, image_url TEXT, episodes INTEGER, status TEXT,
                UNIQUE(user_id, anime_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                user_id INTEGER, query TEXT, anime_id INTEGER, timestamp DATETIME
            )
        """)
        # ⚡ ТАБЛИЦА КЕША (Чтобы не ждать API)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                url TEXT PRIMARY KEY,
                response TEXT,
                timestamp REAL
            )
        """)
        await db.commit()

# =========================================================
# 🚀 API КЛИЕНТ С КЕШЕМ
# =========================================================
class CachedClient:
    def __init__(self):
        self.headers = {'User-Agent': 'UltraBot/4.0', 'Accept': 'application/json'}

    async def _request(self, endpoint, params=None):
        url = f"{SHIKI_API}/{endpoint}"
        if params:
            url += "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        
        # 1. Проверяем кеш (Время жизни: 3 часа)
        async with aiosqlite.connect('anime_ultra.db') as db:
            async with db.execute("SELECT response, timestamp FROM api_cache WHERE url=?", (url,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    data, ts = row
                    if time.time() - ts < 10800: # 3 часа
                        return json.loads(data)

        # 2. Если нет в кеше — делаем запрос
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # 3. Сохраняем в кеш
                        async with aiosqlite.connect('anime_ultra.db') as db:
                            await db.execute("INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?)", 
                                           (url, json.dumps(data), time.time()))
                            await db.commit()
                        return data
            except Exception as e:
                logging.error(f"Network Error: {e}")
        return None

    async def search(self, query):
        return await self._request("animes", {'search': query, 'limit': 10, 'order': 'popularity'})

    async def details(self, aid):
        return await self._request(f"animes/{aid}")

    async def screens(self, aid):
        return await self._request(f"animes/{aid}/screenshots")
    
    async def similar(self, aid):
        return await self._request(f"animes/{aid}/similar")

api = CachedClient()

# =========================================================
# 🧠 ЛОГИКА БОТА
# =========================================================
router = Router()

class States(StatesGroup):
    search = State()
    chat = State()

# --- Helpers ---
def get_best_image(data):
    base = SHIKI_URL
    imgs = data.get('image', {})
    # Берем original для качества, но если нет - preview
    url = imgs.get('original') or imgs.get('preview') or imgs.get('x96')
    return base + url if url else "https://via.placeholder.com/400x600?text=No+Image"

def clean_desc(text):
    if not text: return "Описание отсутствует."
    text = re.sub(r'<[^>]+>', '', text)
    return text[:750] + "..." if len(text) > 750 else text

# --- Handlers ---

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎 Поиск Аниме", callback_data="start_search")
    kb.button(text="🔥 Тренды", callback_data="get_trends")
    kb.button(text="🎲 Рандом", callback_data="get_random")
    kb.button(text="🤖 AI Чат", callback_data="start_ai")
    kb.button(text="❤️ Избранное", callback_data="my_favs")
    kb.button(text="👁 История", callback_data="my_history")
    kb.adjust(2)
    
    await message.answer(
        f"⚡ <b>Ultra Anime Bot v4.0</b>\n"
        f"Привет, {message.from_user.first_name}! Я стал быстрее и умнее.\n\n"
        f"🚀 <b>Мои фишки:</b>\n"
        f"• Поиск сразу по 8+ базам\n"
        f"• Мгновенные ответы (Кеширование)\n"
        f"• HD Картинки и Трейлеры\n\n"
        f"👇 Жми кнопку:", 
        reply_markup=kb.as_markup()
    )

# --- ПОИСК (КАРУСЕЛЬ) ---
@router.callback_query(F.data == "start_search")
async def ask_query(clb: types.CallbackQuery, state: FSMContext):
    await clb.message.answer("✍️ <b>Напиши название аниме:</b>")
    await state.set_state(States.search)
    await clb.answer()

@router.message(States.search)
async def perform_search(message: types.Message, state: FSMContext):
    res = await api.search(message.text)
    if not res:
        await message.answer("❌ Ничего не найдено.")
        return

    # Сохраняем результаты в FSM, чтобы листать
    await state.update_data(results=res, index=0)
    await show_search_result(message, res[0], 0, len(res))

async def show_search_result(message, anime, index, total, is_edit=False):
    kb = InlineKeyboardBuilder()
    kb.button(text="📂 ОТКРЫТЬ", callback_data=f"open_{anime['id']}")
    kb.button(text="🎬 СМОТРЕТЬ", callback_data=f"watch_{anime['id']}")
    
    # Кнопки листания
    btns = []
    if index > 0: btns.append(InlineKeyboardButton(text="⬅️", callback_data="prev_res"))
    btns.append(InlineKeyboardButton(text=f"{index+1}/{total}", callback_data="noop"))
    if index < total - 1: btns.append(InlineKeyboardButton(text="➡️", callback_data="next_res"))
    kb.row(*btns)
    kb.button(text="❌ Отмена", callback_data="cancel_search")
    
    caption = (
        f"🔎 <b>Результат поиска:</b>\n\n"
        f"📺 <b>{anime['russian'] or anime['name']}</b>\n"
        f"⭐️ Оценка: {anime.get('score', '?')}\n"
        f"📅 Год: {anime.get('aired_on', '????')[:4]}\n"
        f"💿 Тип: {anime.get('kind', 'tv').upper()}"
    )
    
    img = get_best_image(anime)
    media = InputMediaPhoto(media=img, caption=caption, parse_mode=ParseMode.HTML)
    
    if is_edit:
        await message.edit_media(media, reply_markup=kb.as_markup())
    else:
        await message.answer_photo(img, caption=caption, reply_markup=kb.as_markup())

# --- ЛИСТАНИЕ РЕЗУЛЬТАТОВ ---
@router.callback_query(F.data.in_({"next_res", "prev_res"}))
async def cycle_results(clb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    res = data.get("results")
    idx = data.get("index", 0)
    
    if clb.data == "next_res": idx += 1
    else: idx -= 1
    
    await state.update_data(index=idx)
    await show_search_result(clb.message, res[idx], idx, len(res), is_edit=True)
    await clb.answer()

@router.callback_query(F.data == "cancel_search")
async def cancel(clb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await clb.message.delete()

# --- ПОДРОБНОСТИ ---
@router.callback_query(F.data.startswith("open_"))
async def show_details(clb: types.CallbackQuery):
    aid = clb.data.split("_")[1]
    data = await api.details(aid)
    
    if not data: return await clb.answer("Ошибка загрузки", show_alert=True)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 СМОТРЕТЬ ОНЛАЙН", callback_data=f"watch_{aid}")
    kb.button(text="🖼 Кадры", callback_data=f"shots_{aid}")
    kb.button(text="🔗 Похожее", callback_data=f"sim_{aid}")
    kb.button(text="❤️ В избранное", callback_data=f"fav_{aid}")
    kb.button(text="🔙 Назад", callback_data="cancel_search") # Или в меню
    kb.adjust(1, 2, 1, 1)

    text = (
        f"🇯🇵 <b>{data['name']}</b>\n"
        f"🇷🇺 <b>{data['russian']}</b>\n\n"
        f"📊 Рейтинг: <b>{data.get('score', 0)}</b> | Эпизодов: {data.get('episodes', '?')}\n"
        f"🎭 Жанры: {', '.join([g['russian'] for g in data.get('genres', [])])}\n\n"
        f"📝 <b>Сюжет:</b>\n{clean_desc(data.get('description'))}"
    )
    
    try:
        await clb.message.delete()
        await clb.message.answer_photo(get_best_image(data), caption=text, reply_markup=kb.as_markup())
    except:
        await clb.message.answer(text, reply_markup=kb.as_markup())

# --- МЕГА-ПОИСК (WATCH) ---
@router.callback_query(F.data.startswith("watch_"))
async def watch_hup(clb: types.CallbackQuery):
    aid = clb.data.split("_")[1]
    data = await api.details(aid)
    title = data['russian'] or data['name']
    
    # Сохраняем в историю
    async with aiosqlite.connect('anime_ultra.db') as db:
        await db.execute("INSERT INTO history VALUES (?, ?, ?, ?)", 
                        (clb.from_user.id, title, aid, datetime.now()))
        await db.commit()

    kb = InlineKeyboardBuilder()
    # Генерируем кнопки для всех сайтов
    for name, url_base in WATCH_SITES.items():
        # Формируем правильный запрос (удаляем спецсимволы)
        clean_q = re.sub(r'[^\w\s]', '', title)
        kb.button(text=name, url=f"{url_base}{clean_q}")
    
    kb.button(text="🔙 Назад к инфо", callback_data=f"open_{aid}")
    kb.adjust(2) # По 2 в ряд
    
    await clb.message.answer(
        f"🚀 <b>Глобальный поиск: {title}</b>\n\n"
        f"Я подготовил прямые ссылки на поиск в лучших онлайн-кинотеатрах.\n"
        f"💡 <i>Если нет на одном — точно есть на другом!</i>",
        reply_markup=kb.as_markup()
    )
    await clb.answer()

# --- ДОП ФУНКЦИИ (Screens) ---
@router.callback_query(F.data.startswith("shots_"))
async def get_shots(clb: types.CallbackQuery):
    aid = clb.data.split("_")[1]
    shots = await api.screens(aid)
    if not shots: return await clb.answer("Кадров нет", show_alert=True)
    
    media = [InputMediaPhoto(media=SHIKI_URL + s['original']) for s in shots[:4]]
    await clb.message.answer_media_group(media)
    await clb.answer()

@router.callback_query(F.data.startswith("sim_"))
async def get_similar(clb: types.CallbackQuery):
    aid = clb.data.split("_")[1]
    sims = await api.similar(aid)
    if not sims: return await clb.answer("Похожих нет", show_alert=True)
    
    text = "🔗 <b>Похожие аниме:</b>\n"
    for s in sims[:6]:
        text += f"• {s['russian']} ({s.get('score', '?')})\n"
    await clb.message.answer(text)
    await clb.answer()

# --- ИЗБРАННОЕ ---
@router.callback_query(F.data.startswith("fav_"))
async def toggle_fav(clb: types.CallbackQuery):
    aid = int(clb.data.split("_")[1])
    uid = clb.from_user.id
    
    async with aiosqlite.connect('anime_ultra.db') as db:
        res = await db.execute("SELECT 1 FROM favorites WHERE user_id=? AND anime_id=?", (uid, aid))
        if await res.fetchone():
            await db.execute("DELETE FROM favorites WHERE user_id=? AND anime_id=?", (uid, aid))
            await clb.answer("🗑 Удалено")
        else:
            data = await api.details(aid)
            await db.execute("INSERT INTO favorites VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (uid, aid, data['russian'], float(data.get('score',0)), 
                            get_best_image(data), data.get('episodes',0), data.get('status','')))
            await clb.answer("❤️ Сохранено")
        await db.commit()

@router.callback_query(F.data == "my_favs")
async def show_favs(clb: types.CallbackQuery):
    async with aiosqlite.connect('anime_ultra.db') as db:
        async with db.execute("SELECT title, anime_id FROM favorites WHERE user_id=?", (clb.from_user.id,)) as cur:
            rows = await cur.fetchall()
            
    if not rows: return await clb.answer("Пусто", show_alert=True)
    
    kb = InlineKeyboardBuilder()
    for title, aid in rows:
        kb.button(text=f"⭐ {title[:20]}", callback_data=f"open_{aid}")
    kb.button(text="🔙 В меню", callback_data="back_home")
    kb.adjust(1)
    await clb.message.answer("❤️ <b>Твое избранное:</b>", reply_markup=kb.as_markup())

# --- ИСТОРИЯ ---
@router.callback_query(F.data == "my_history")
async def show_history(clb: types.CallbackQuery):
    async with aiosqlite.connect('anime_ultra.db') as db:
        # Уникальные записи последних просмотров
        async with db.execute("SELECT DISTINCT query, anime_id FROM history WHERE user_id=? ORDER BY timestamp DESC LIMIT 10", (clb.from_user.id,)) as cur:
            rows = await cur.fetchall()
    
    if not rows: return await clb.answer("История пуста", show_alert=True)
    kb = InlineKeyboardBuilder()
    for t, aid in rows:
        kb.button(text=f"👁 {t[:20]}", callback_data=f"open_{aid}")
    kb.button(text="🔙 В меню", callback_data="back_home")
    kb.adjust(1)
    await clb.message.answer("🕰 <b>История просмотров:</b>", reply_markup=kb.as_markup())

# --- AI ЧАТ ---
@router.callback_query(F.data == "start_ai")
async def start_ai(clb: types.CallbackQuery, state: FSMContext):
    if not ai_model: return await clb.answer("AI выключен", show_alert=True)
    await clb.message.answer("🤖 <b>AI на связи!</b> Спроси что-нибудь про аниме.")
    await state.set_state(States.chat)
    await clb.answer()

@router.message(States.chat)
async def ai_response(message: types.Message, state: FSMContext):
    msg = await message.answer("⏳ ...")
    try:
        resp = await ai_model.generate_content_async(
            f"Ты аниме-эксперт. Ответь кратко на русском: {message.text}"
        )
        await msg.edit_text(resp.text, parse_mode=ParseMode.MARKDOWN)
    except:
        await msg.edit_text("Ошибка AI")
    await state.clear()

@router.callback_query(F.data == "back_home")
async def go_home(clb: types.CallbackQuery):
    await cmd_start(clb.message)

@router.callback_query(F.data == "noop")
async def noop(clb: types.CallbackQuery):
    await clb.answer()

# --- ЗАПУСК ---
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    
    print("🚀 ULTRA SPEED BOT STARTED")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR) # Меньше мусора в логах
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped")