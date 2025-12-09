import asyncio
import logging
import os
import re
import datetime
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, ChatPermissions, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, FSInputFile, ContentType
)
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web  # Додали для фейкового сервера
from aiogram.types import LabeledPrice, PreCheckoutQuery, BufferedInputFile
import analytics

import database as db
import word_list
import image_checker 

# --- ЗМІНИ ТУТ ---
# Беремо токен зі змінних оточення (на сервері), або використовуємо твій хардкод для локального тесту
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

if not os.path.exists("temp_photos"):
    os.makedirs("temp_photos")

# ... (ВЕСЬ ТВІЙ КОД ФІЛЬТРІВ, ЛОГІВ, АДМІНКИ ЗАЛИШАЄТЬСЯ БЕЗ ЗМІН) ...
# ... (від LINK_REGEX до global_listener включно) ...

# ... (після global_listener вставляємо цей новий фінал) ...

# --- ФЕЙКОВИЙ ВЕБ-СЕРВЕР ДЛЯ KOYEB ---
async def health_check(request):
    return web.Response(text="Bot is running OK!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    # Koyeb дає порт через змінну PORT, або використовуємо 8000
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

if not os.path.exists("temp_photos"):
    os.makedirs("temp_photos")

# Регулярка для пошуку посилань
LINK_REGEX = re.compile(r'(https?://|t\.me/|www\.)\S+', re.IGNORECASE)

# --- ФІЛЬТРИ ---
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type == "private": return False
        member = await message.chat.get_member(message.from_user.id)
        return member.status in ("administrator", "creator")

# --- ЛОГУВАННЯ В ЛІЧКУ ---
async def send_log(message: Message, violation_type: str, action: str, file_path: str = None, is_report: bool = False):
    chat_id = message.chat.id
    
    # Шукаємо, кому відправити лог для цього чату
    receiver_id = await db.get_log_receiver(chat_id)
    if not receiver_id: return # Ніхто не підписався на логи

    user = message.from_user
    chat = message.chat
    
    prefix = "🚨 <b>СКАРГА (REPORT)</b>" if is_report else "🛡 <b>МОДЕРАЦІЯ</b>"
    
    text = (
        f"{prefix}\n"
        f"👤 <b>Хто:</b> {user.full_name} (<code>{user.id}</code>)\n"
        f"🏠 <b>Де:</b> {chat.title}\n"
        f"⚠️ <b>Що:</b> {violation_type}\n"
        f"🔨 <b>Дія:</b> {action}"
    )

    if message.text:
        text += f"\n📝 <b>Текст:</b> {message.text}"

    try:
        if file_path and os.path.exists(file_path):
            await bot.send_photo(chat_id=receiver_id, photo=FSInputFile(file_path), caption=text, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=receiver_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"Не вдалося відправити лог адміну {receiver_id}: {e}")

# --- ПОКАРАННЯ ---
async def punish_user(message: Message, violation_type: str, file_path: str = None):
    user_id = message.from_user.id
    chat_id = message.chat.id
    name = message.from_user.full_name
    
    w_normal, w_heavy, _ = await db.get_user_stats(user_id, chat_id)
    
    # Логіка оновлення варнів
    if violation_type == "heavy": w_heavy += 1
    else: w_normal += 1
    
    # Перевірка на бан
    trigger_ban = False
    reason = ""
    if w_heavy >= 2:
        trigger_ban, reason = True, "2 тяжких"
    elif w_normal >= 3:
        trigger_ban, reason = True, "3 звичайних"
    elif w_heavy >= 1 and w_normal >= 2:
        trigger_ban, reason = True, "Комбо (1 тяжке + 2 звичайних)"

    # Логуємо
    action_log = f"Попередження ({w_normal}/{w_heavy})"
    if trigger_ban: action_log = "МУТ/БАН"
    await send_log(message, violation_type, action_log, file_path)

    # Видаляємо
    try: await message.delete()
    except: pass

    # Повідомляємо в чат
    msg_text = f"❗️ {name}, порушення! ({violation_type})"
    await message.answer(msg_text)

    if trigger_ban:
        await db.add_temp_ban_count(user_id, chat_id)
        _, _, updated_temp_bans = await db.get_user_stats(user_id, chat_id)

        if updated_temp_bans >= 3:
            await bot.ban_chat_member(chat_id, user_id)
            await message.answer(f"⛔️ {name} -> <b>Довічний бан</b> (3 мути).", parse_mode="HTML")
        else:
            mins = await db.get_ban_duration(chat_id)
            until = datetime.datetime.now() + datetime.timedelta(minutes=mins)
            try:
                await bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                await message.answer(f"🚫 {name} -> <b>Мут на {mins} хв.</b>\nПричина: {reason}", parse_mode="HTML")
            except Exception as e:
                print(f"Err mute: {e}")
    else:
        await db.update_warns(user_id, chat_id, w_normal, w_heavy)

# --- ДОПОМІЖНА ДЛЯ МЕДІА ---
async def process_media_check(message: Message, file_id: str):
    file_path = f"temp_photos/{file_id}.jpg" 
    try:
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, file_path)
        violation = await image_checker.check_image_content(file_path)
        if violation:
            await punish_user(message, violation, file_path)
            return True
    except Exception as e:
        print(f"Error media check: {e}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)
    return False

# ==========================================
# 1. КАПЧА (ВІТАННЯ НОВАЧКІВ)
# ==========================================
@router.message(F.new_chat_members)
async def on_user_join(message: Message):
    for user in message.new_chat_members:
        if user.is_bot: continue
        
        # Одразу даємо мут
        try:
            await bot.restrict_chat_member(
                message.chat.id, 
                user.id, 
                permissions=ChatPermissions(can_send_messages=False)
            )
            
            # Кнопка підтвердження
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Я не бот", callback_data=f"captcha:{user.id}")]
            ])
            
            await message.answer(
                f"👋 Привіт, {user.full_name}!\nНатисни кнопку нижче, щоб писати в чаті.", 
                reply_markup=kb
            )
        except Exception as e:
            print(f"Не вдалося видати капчу: {e}")

@router.callback_query(F.data.startswith("captcha:"))
async def on_captcha_click(callback: CallbackQuery):
    user_id_in_button = int(callback.data.split(":")[1])
    
    if callback.from_user.id != user_id_in_button:
        await callback.answer("Це кнопка не для тебе!", show_alert=True)
        return
    
    # Знімаємо мут
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_invite_users=True
        )
        await bot.restrict_chat_member(callback.message.chat.id, callback.from_user.id, permissions=permissions)
        await callback.message.delete() # Видаляємо повідомлення з капчею
        await callback.answer("Велкам! ✅")
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)

# ==========================================
# 3. СИСТЕМА РЕПОРТІВ (/report)
# ==========================================
@router.message(Command("report"))
async def cmd_report(message: Message):
    # Видаляємо команду
    try: await message.delete()
    except: pass

    if not message.reply_to_message:
        msg = await message.answer("⚠️ Пиши <code>/report</code> у відповідь на повідомлення!", parse_mode="HTML")
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass
        return

    # Не можна репортити бота або адмінів
    if message.reply_to_message.from_user.id == bot.id: return
    
    # 1. Зберігаємо репорт у БД
    await db.add_report(
        chat_id=message.chat.id,
        message_id=message.reply_to_message.message_id,
        user_id=message.reply_to_message.from_user.id,
        reporter_id=message.from_user.id
    )

    # 2. Сповіщаємо адміна (якщо увімкнені логи)
    receiver_id = await db.get_log_receiver(message.chat.id)
    if receiver_id:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚩 Переглянути скарги", callback_data=f"show_reports:{message.chat.id}")]
            ])
            await bot.send_message(
                receiver_id, 
                f"🚨 <b>Нова скарга!</b>\nЧат: {message.chat.title}\nВід: {message.from_user.full_name}",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except: pass
    
    # 3. Кажемо користувачу, що все ок
    await message.answer("✅ Скарга прийнята.", delete_after=5)

# ==========================================
# ВИПРАВЛЕНА АДМІН-ПАНЕЛЬ (ШВИДКА)
# ==========================================

# 1. Список чатів
@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin_panel(message: Message):
    all_chats = await db.get_all_chats()
    if not all_chats:
        return await message.answer("Я ще не знаю жодного чату. Додай мене в групу!")

    keyboard = []
    for chat_id, chat_title in all_chats:
        keyboard.append([InlineKeyboardButton(text=f"📢 {chat_title}", callback_data=f"menu_main:{chat_id}")])

    await message.answer("Обери групу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

# 2. Головне меню чату
@router.callback_query(F.data.startswith("menu_main:"))
async def cb_menu_main(callback: CallbackQuery):
    try: await callback.answer()
    except: pass

    chat_id = int(callback.data.split(":")[1])
    
    # Перевіряємо логи
    current_receiver = await db.get_log_receiver(chat_id)
    is_me = (current_receiver == callback.from_user.id)
    log_status = "✅ УВІМКНЕНО" if is_me else "❌ ВИМКНЕНО"
    
    # Рахуємо кількість скарг
    reports_count = await db.get_reports_count(chat_id)
    reports_text = f"🚩 Скарги ({reports_count})" if reports_count > 0 else "🚩 Скарги (0)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=reports_text, callback_data=f"show_reports:{chat_id}")],
        [InlineKeyboardButton(text="⚙️ Налаштувати час бану", callback_data=f"menu_settings:{chat_id}")],
        [InlineKeyboardButton(text=f"📊 Логи в ЛС ({log_status})", callback_data=f"toggle_logs:{chat_id}")],
        [InlineKeyboardButton(text="🔙 Назад до списку", callback_data="back_to_list")]
    ])
    
    try:
        await callback.message.edit_text(
            f"🔧 <b>Керування групою</b>\nID: <code>{chat_id}</code>", 
            reply_markup=kb, parse_mode="HTML"
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower(): print(f"Error menu: {e}")

# 3. Перемикач логів
@router.callback_query(F.data.startswith("toggle_logs:"))
async def cb_toggle_logs(callback: CallbackQuery):
    # Спочатку відповідаємо!
    await callback.answer("Перемикаю...")
    
    chat_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    
    current_receiver = await db.get_log_receiver(chat_id)
    
    # Логіка перемикання
    if current_receiver == user_id:
        await db.set_log_receiver(chat_id, 0) # Вимикаємо
    else:
        await db.set_log_receiver(chat_id, user_id) # Вмикаємо

    # Оновлюємо меню (викликаємо функцію меню вручну)
    # Але оскільки там теж є callback.answer, ми просто оновимо текст тут, щоб не було конфліктів
    
    # Оновлюємо статус для відображення
    new_receiver = await db.get_log_receiver(chat_id)
    log_status = "✅ УВІМКНЕНО" if new_receiver == user_id else "❌ ВИМКНЕНО"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Налаштувати час бану", callback_data=f"menu_settings:{chat_id}")],
        [InlineKeyboardButton(text=f"📊 Логи в ЛС ({log_status})", callback_data=f"toggle_logs:{chat_id}")],
        [InlineKeyboardButton(text="🔙 Назад до списку", callback_data="back_to_list")]
    ])
    
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            print(f"Error toggle logs: {e}")


# 4. Меню налаштувань часу
@router.callback_query(F.data.startswith("menu_settings:"))
async def cb_menu_settings(callback: CallbackQuery):
    try: await callback.answer()
    except: pass

    chat_id = int(callback.data.split(":")[1])
    duration = await db.get_ban_duration(chat_id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 30 хв", callback_data=f"set_ban:{chat_id}:30"),
         InlineKeyboardButton(text="⏱ 60 хв", callback_data=f"set_ban:{chat_id}:60")],
        [InlineKeyboardButton(text="⏱ 24 год", callback_data=f"set_ban:{chat_id}:1440"),
         InlineKeyboardButton(text="🔙 Назад в меню групи", callback_data=f"menu_main:{chat_id}")]
    ])
    
    try:
        await callback.message.edit_text(
            f"⏱ <b>Налаштування часу</b>\nПоточний бан: <b>{duration} хв</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            print(f"Error settings: {e}")

# 5. Обробка вибору часу
@router.callback_query(F.data.startswith("set_ban:"))
async def cb_set_ban(callback: CallbackQuery):
    await callback.answer("Час збережено! ✅")
    
    parts = callback.data.split(":")
    chat_id = int(parts[1])
    minutes = int(parts[2])
    
    await db.set_ban_duration(chat_id, minutes)
    
    # Оновлюємо текст (залишаємось в тому ж меню)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 30 хв", callback_data=f"set_ban:{chat_id}:30"),
         InlineKeyboardButton(text="⏱ 60 хв", callback_data=f"set_ban:{chat_id}:60")],
        [InlineKeyboardButton(text="⏱ 24 год", callback_data=f"set_ban:{chat_id}:1440"),
         InlineKeyboardButton(text="🔙 Назад в меню групи", callback_data=f"menu_main:{chat_id}")]
    ])
    
    try:
        await callback.message.edit_text(
            f"✅ <b>Збережено!</b>\n⏱ <b>Налаштування часу</b>\nПоточний бан: <b>{minutes} хв</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            print(f"Error set ban: {e}")

# Назад до списку
@router.callback_query(F.data == "back_to_list")
async def cb_back_list(callback: CallbackQuery):
    try: await callback.answer()
    except: pass
    
    await callback.message.delete()
    await cmd_admin_panel(callback.message)

@router.callback_query(F.data.startswith("show_reports:"))
async def cb_show_reports(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[1])
    
    # Отримуємо всі репорти
    reports = await db.get_active_reports(chat_id)
    
    if not reports:
        await callback.answer("Ура! Активних скарг немає.", show_alert=True)
        # Оновлюємо меню, щоб скинути лічильник на кнопці
        return await cb_menu_main(callback)

    # Беремо найпершу скаргу (FIFO - First In, First Out)
    report = reports[0] 
    
    # Кнопки дій для адміна
    # Формат: дія:chat_id:user_id:message_id:report_id
    base_data = f"{chat_id}:{report['user_id']}:{report['message_id']}:{report['report_id']}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="😶 Мут", callback_data=f"act_mute:{base_data}"),
            InlineKeyboardButton(text="🔨 Бан", callback_data=f"act_ban:{base_data}")
        ],
        [
            InlineKeyboardButton(text="🗑 Видалити повідомлення", callback_data=f"act_del:{base_data}")
        ],
        [
            InlineKeyboardButton(text="❌ Пропустити (видалити репорт)", callback_data=f"act_skip:{report['report_id']}:{chat_id}")
        ],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data=f"menu_main:{chat_id}")]
    ])

    await callback.message.delete() # Видаляємо старе меню, бо ми будемо слати копію повідомлення
    
    # Головна магія: копіюємо повідомлення порушника адміну в лічку
    # ... всередині cb_show_reports ...

    # Головна магія: копіюємо повідомлення
    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=chat_id,
            message_id=report['message_id'],
            caption="🔻 <b>ОСЬ НА ЩО ПОСКАРЖИЛИСЬ</b> 🔻",
            parse_mode="HTML"
        )
    except Exception as e:
        # ПЛАН Б: Якщо не вдалося скопіювати (наприклад, приватність)
        await callback.message.answer(
            f"⚠️ <b>Не вдалося показати повідомлення.</b>\n"
            f"Причина: повідомлення видалено або у користувача закритий профіль.\n"
            f"Але ID порушника в мене є: <code>{report['user_id']}</code>", 
            parse_mode="HTML"
        )
    # Пишемо інфо і даємо кнопки
    await callback.message.answer(
        f"🚨 <b>РОЗГЛЯД СКАРГИ #{report['report_id']}</b>\n"
        f"Порушник ID: <code>{report['user_id']}</code>\n"
        f"Оберіть дію:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

# Кнопка "Інструкція" (ОНОВЛЕНА)
@router.callback_query(F.data == "show_help")
async def cb_help(callback: CallbackQuery):
    # Оновлений текст інструкції
    instruction_text = (
        "📚 <b>ЯК НАЛАШТУВАТИ БОТА:</b>\n\n"
        "<b>Крок 1: Підключення</b>\n"
        "1. Додайте мене у вашу групу.\n"
        "2. <b>Призначте Адміністратором</b> (мені потрібні права видаляти повідомлення і банити).\n\n"
        "<b>Крок 2: Активація</b>\n"
        "3. Напишіть у групі <b>будь-яке повідомлення</b> (наприклад: 'привіт').\n"
        "<i>Це потрібно, щоб я зберіг вашу групу в базу даних.</i>\n\n"
        "<b>Крок 3: Налаштування</b>\n"
        "4. Поверніться сюди (в особисті повідомлення).\n"
        "5. Натисніть кнопку <b>'⚙️ Адмінка'</b> або напишіть /admin.\n"
        "6. Оберіть чат і натисніть <b>'📊 Логи в ЛС'</b>, щоб бачити звіти."
    )
    
    # Кнопки навігації
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати в групу", url=f"https://t.me/{(await bot.get_me()).username}?startgroup=true")],
        [InlineKeyboardButton(text="⚙️ Адмінка (Крок 3)", callback_data="back_to_list")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_start")]
    ])
    
    # Редагуємо повідомлення
    await callback.message.edit_text(instruction_text, reply_markup=kb, parse_mode="HTML")

# ОБРОБКА ДІЙ (БАН, МУТ, ВИДАЛИТИ, ПРОПУСТИТИ)
@router.callback_query(F.data.startswith("act_"))
async def cb_report_actions(callback: CallbackQuery):
    action = callback.data.split(":")[0] # act_ban, act_mute...
    
    # Обробка кнопки "Пропустити" (вона має інший формат даних)
    if action == "act_skip":
        report_id = int(callback.data.split(":")[1])
        chat_id = int(callback.data.split(":")[2])
        await db.delete_report(report_id)
        await callback.answer("Репорт видалено ✅")
        # Повертаємось до списку (покаже наступний репорт)
        callback.data = f"show_reports:{chat_id}"
        return await cb_show_reports(callback)

    # Розбираємо дані для бан/мут/дел
    # data format: action:chat_id:user_id:message_id:report_id
    parts = callback.data.split(":")
    chat_id = int(parts[1])
    user_id = int(parts[2])
    message_id = int(parts[3])
    report_id = int(parts[4])

    try:
        if action == "act_mute":
            # Мут на час з налаштувань
            mins = await db.get_ban_duration(chat_id)
            until = datetime.datetime.now() + datetime.timedelta(minutes=mins)
            await bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
            await bot.send_message(chat_id, f"🛡 Адміністратор розглянув скаргу.\n🚫 Користувач отримав мут на {mins} хв.")
            await callback.answer(f"Видано мут на {mins} хв!")

        elif action == "act_ban":
            # Бан і кік
            await bot.ban_chat_member(chat_id, user_id)
            await bot.send_message(chat_id, f"🛡 Адміністратор розглянув скаргу.\n⛔️ Користувач забанений.")
            await callback.answer("Користувача забанено!")

        elif action == "act_del":
            # Просто видалити повідомлення
            await bot.delete_message(chat_id, message_id)
            await callback.answer("Повідомлення видалено!")

        # Після будь-якої дії видаляємо репорт з БД
        await db.delete_report(report_id)
        
        # І переходимо до наступного репорту
        callback.data = f"show_reports:{chat_id}"
        await cb_show_reports(callback)

    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)

# ==========================================
# UNBAN
# ==========================================
@router.message(Command("unban"), IsAdmin())
async def cmd_unban(message: Message):
    if not message.reply_to_message: return
    user = message.reply_to_message.from_user
    chat_id = message.chat.id
    try:
        # Unban + Unmute
        await bot.unban_chat_member(chat_id, user.id, only_if_banned=True)
        permissions = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True)
        await bot.restrict_chat_member(chat_id, user.id, permissions=permissions)
        await db.reset_user(user.id, chat_id)
        await message.answer(f"✅ {user.full_name} помилуваний.")
    except Exception as e:
        await message.answer(f"Помилка: {e}")

# ==========================================
# 0. СТАРТОВЕ МЕНЮ (ПОКРАЩЕНЕ)
# ==========================================

@router.message(Command("start"))
async def cmd_start(message: Message):
    # --- ЛОГІКА ДЛЯ ГРУП ---
    if message.chat.type != "private":
        await message.answer("🛡 <b>ModBot активовано!</b>\nЯ слідкую за порядком.", parse_mode="HTML")
        # Оновлюємо назву чату в БД, щоб адмінка працювала коректно
        if message.chat.title:
            await db.update_chat_title(message.chat.id, message.chat.title)
        return

    # --- ЛОГІКА ДЛЯ ОСОБИСТИХ ПОВІДОМЛЕНЬ (КРАСИВЕ МЕНЮ) ---
    user_name = message.from_user.first_name
    
    text = (
        f"👋 <b>Привіт, {user_name}!</b>\n\n"
        "Я — <b>Ultimate Moderator Bot</b>, твій персональний охоронець чатів. 🛡\n"
        "Забудь про ручне видалення спаму та ботів. Я зроблю це за тебе.\n\n"
        "🚀 <b>Мої можливості:</b>\n"
        "├ 🤖 <b>Розумна Капча:</b> Жодних арабських ботів.\n"
        "├ 🌊 <b>Анти-флуд:</b> Мут за спам повідомленнями.\n"
        "├ 🔞 <b>AI-Зір:</b> Видаляю порно (фото/стікери/гіф).\n"
        "├ 🔗 <b>Анти-Лінке:</b> Видаляю рекламу в посиланнях.\n"
        "└ 🧹 <b>Клінінг:</b> Видаляю повідомлення 'Вступив/Вийшов'.\n\n"
        "💎 <b>Premium-фішки:</b>\n"
        "└ 📊 <b>Графічна аналітика</b> активності чату.\n\n"
        "👇 <b>Обери дію в меню:</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Як підключити (Інструкція)", callback_data="show_help")],
        [
            InlineKeyboardButton(text="💎 Купити Premium", callback_data="buy_premium"),
            InlineKeyboardButton(text="⚙️ Адмінка", callback_data="back_to_list") # Це веде до вибору груп
        ],
        [InlineKeyboardButton(text="➕ Додати мене в чат", url=f"https://t.me/{(await bot.get_me()).username}?startgroup=true")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# ==========================================
# 💎 ПРЕМІУМ ФУНКЦІЇ (ANTI-FLOOD & CLEANER)
# ==========================================

# Кеш для анти-флуду: зберігає час повідомлень {user_id: [time1, time2...]}
FLOOD_CACHE = {} 
FLOOD_LIMIT = 5   # Максимум повідомлень
FLOOD_TIME = 10   # За скільки секунд (вікно перевірки)

async def check_flood(message: Message) -> bool:
    """
    Перевіряє, чи не флудить користувач. 
    Повертає True, якщо користувача замучено.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id
    now = datetime.datetime.now().timestamp()

    # Якщо користувача немає в кеші - створюємо список
    if user_id not in FLOOD_CACHE:
        FLOOD_CACHE[user_id] = []

    # Додаємо час поточного повідомлення
    FLOOD_CACHE[user_id].append(now)

    # Залишаємо тільки свіжі повідомлення (не старші за FLOOD_TIME)
    FLOOD_CACHE[user_id] = [t for t in FLOOD_CACHE[user_id] if now - t < FLOOD_TIME]

    # Перевіряємо кількість
    if len(FLOOD_CACHE[user_id]) > FLOOD_LIMIT:
        # Очищаємо кеш, щоб не банити його знову кожну секунду
        FLOOD_CACHE[user_id] = []
        
        try:
            # Видаємо МУТ на 10 хвилин
            mins = 10
            until = datetime.datetime.now() + datetime.timedelta(minutes=mins)
            permissions = ChatPermissions(can_send_messages=False)
            
            await bot.restrict_chat_member(chat_id, user_id, permissions=permissions, until_date=until)
            
            # Повідомляємо (і видаляємо це повідомлення через 5 сек)
            msg = await message.answer(f"🌊 {message.from_user.full_name}, не флуди! Охолонь {mins} хв.")
            await asyncio.sleep(5)
            await msg.delete()
            return True # Флуд виявлено
            
        except Exception as e:
            print(f"Не вдалося видати мут за флуд: {e}")
            
    return False

# 🧹 Авто-чистка системних повідомлень
# Видаляє: "Вступив у групу", "Покинув групу", "Закріпив повідомлення"
@router.message(F.content_type.in_({
    ContentType.NEW_CHAT_MEMBERS, 
    ContentType.LEFT_CHAT_MEMBER, 
    ContentType.PINNED_MESSAGE
}))
async def clean_service_messages(message: Message):
    try:
        await message.delete()
    except Exception as e:
        # Іноді повідомлення вже видалено або немає прав
        pass

# ==========================================
# 💰 PREMIUM & ПЛАТЕЖІ
# ==========================================

# 1. Кнопка "Купити Premium"

@router.callback_query(F.data == "buy_premium")
async def cb_buy_premium(callback: CallbackQuery):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Premium (30 днів)",
        description="Доступ до графіків та аналітики.",
        payload="month_sub_payload",
        provider_token="", # <--- ДЛЯ STARS ЗАЛИШАЄМО ПОРОЖНІМ!
        currency="XTR",    # <--- ВАЛЮТА - ЗІРКИ
        prices=[
            # Ціна в кількості зірок. 100 Stars ≈ $1.30 - $1.50
            LabeledPrice(label="Підписка", amount=200) 
        ],
        start_parameter="buy_premium"
    )
    await callback.answer()

# 2. Pre-Checkout (Обов'язкова перевірка перед списанням грошей)
@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Тут можна перевірити, чи є товар в наявності (у нас підписка - завжди є)
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# 3. Успішна оплата (Гроші отримано)
@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment_info = message.successful_payment
    
    # Видаємо преміум на 30 днів
    await db.set_premium(message.from_user.id, 30)
    
    await message.answer(
        f"🎉 <b>Оплата пройшла успішно!</b>\n"
        f"Сума: {payment_info.total_amount / 100} {payment_info.currency}\n\n"
        f"✅ Premium активовано до {datetime.datetime.now() + datetime.timedelta(days=30)}.\n"
        f"Тепер спробуйте команду <code>/stats</code> у групі!",
        parse_mode="HTML"
    )

# ==========================================
# 📊 СТАТИСТИКА (Тільки для Premium)
# ==========================================
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    # Працює тільки в групах
    if message.chat.type == "private":
        return await message.answer("Ця команда для групових чатів.")

    # 1. Перевіряємо підписку того, хто викликав
    user_id = message.from_user.id
    has_premium = await db.check_premium(user_id)
    
    if not has_premium:
        # Пропонуємо купити
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Купити Premium", url=f"https://t.me/{(await bot.get_me()).username}?start=premium")]
        ])
        await message.answer(
            "🔒 <b>Ця функція доступна тільки з Premium.</b>\n\n"
            "Купіть підписку, щоб бачити, хто найактивніший у чаті.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    # 2. Генеруємо графік
    wait_msg = await message.answer("📊 Збираю дані та малюю графік...")
    
    try:
        # Отримуємо дані з БД
        top_data = await db.get_top_talkers(message.chat.id, limit=7)
        
        if not top_data:
            await wait_msg.edit_text("📉 У чаті поки немає активності.")
            return

        # Малюємо (це синхронна операція, тому запускаємо в executor, щоб не блокувати бота)
        loop = asyncio.get_running_loop()
        photo_bytes = await loop.run_in_executor(
            None, 
            analytics.create_chart, 
            top_data, 
            f"Активність: {message.chat.title}"
        )
        
        if photo_bytes:
            # Відправляємо картинку
            file = BufferedInputFile(photo_bytes.read(), filename="stats.png")
            await message.answer_photo(file, caption="📈 Топ найактивніших учасників.")
            await wait_msg.delete()
        else:
            await wait_msg.edit_text("Помилка генерації графіка.")
            
    except Exception as e:
        print(f"Stats Error: {e}")
        await wait_msg.edit_text(f"Сталася помилка: {e}")

# ==========================================
# ОСНОВНИЙ СЛУХАЧ (GLOBAL LISTENER)
# ==========================================
@router.message(F.chat.type.in_({"group", "supergroup"}))
async def global_listener(message: Message):
    # 1. РЕЄСТРАЦІЯ ЧАТУ В БД
    # Це виконує пункт 3 інструкції: як тільки хтось пише, бот зберігає ID і назву чату
    if message.chat.title:
        await db.update_chat_title(message.chat.id, message.chat.title)

    # Ігноруємо повідомлення від самого бота
    if message.from_user.id == bot.id: return
    
    # 2. СТАТИСТИКА
    # Рахуємо повідомлення для графіків (Premium)
    await db.increment_message_count(message.from_user.id, message.chat.id)

    # Отримуємо статус користувача (адмін чи ні)
    member = await message.chat.get_member(message.from_user.id)
    is_admin = member.status in ("administrator", "creator")

    # --- 🛡 АНТИ-ФЛУД (Тільки для звичайних користувачів) ---
    if not is_admin:
        # Функція check_flood має бути визначена вище в коді
        is_flooding = await check_flood(message)
        if is_flooding:
            return # Якщо замутили - далі не перевіряємо
    # -------------------------------------------------------

    # Якщо пише адмін - пропускаємо перевірки на спам/мати
    if is_admin: return

    # --- 🔗 АНТИ-ЛІНК (Перевірка посилань) ---
    if message.text or message.caption:
        txt = message.text or message.caption
        if LINK_REGEX.search(txt):
            try: await message.delete()
            except: pass
            
            msg = await message.answer(f"⚠️ {message.from_user.full_name}, посилання заборонені!", delete_after=5)
            return 

    # --- 🤬 ТЕКСТ (Перевірка на мати) ---
    if message.text:
        violation = word_list.check_text_violation(message.text)
        if violation:
            # Функція punish_user має бути визначена вище
            await punish_user(message, violation)
            return

    # --- 🔞 МЕДІА (AI Перевірка фото/стікерів) ---
    file_id = None
    if message.photo: 
        file_id = message.photo[-1].file_id
    elif message.sticker: 
        # Беремо thumbnail (статичну картинку), якщо є
        file_id = message.sticker.thumbnail.file_id if message.sticker.thumbnail else message.sticker.file_id
    elif message.animation and message.animation.thumbnail:
        file_id = message.animation.thumbnail.file_id

    if file_id:
        # Функція process_media_check має бути визначена вище
        await process_media_check(message, file_id)

async def main():
    # 1. Ініціалізація БД (Тільки один раз!)
    await db.init_db()
    
    # 2. Запуск веб-сервера (для Koyeb)
    await start_web_server()
    
    print("Бот (v4.0 Full Pack + Neon DB) запущено...")
    
    # 3. Видаляємо вебхук (на всяк випадок) і запускаємо
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
