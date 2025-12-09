import aiosqlite

DB_NAME = 'moderator.db'

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER,
                chat_id INTEGER,
                warns_normal INTEGER DEFAULT 0,
                warns_heavy INTEGER DEFAULT 0,
                temp_bans_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT,
                ban_time_minutes INTEGER DEFAULT 60,
                log_receiver_id INTEGER DEFAULT NULL
            )
        ''')
        # --- НОВА ТАБЛИЦЯ РЕПОРТІВ ---
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                message_id INTEGER,
                user_id INTEGER,
                reporter_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def update_chat_title(chat_id: int, title: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR IGNORE INTO settings (chat_id, chat_title) VALUES (?, ?)', (chat_id, title))
        await db.execute('UPDATE settings SET chat_title = ? WHERE chat_id = ?', (title, chat_id))
        await db.commit()

async def get_all_chats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT chat_id, chat_title FROM settings') as cursor:
            return await cursor.fetchall()

async def get_user_stats(user_id: int, chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT warns_normal, warns_heavy, temp_bans_count FROM users WHERE user_id = ? AND chat_id = ?', (user_id, chat_id)) as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.execute('INSERT INTO users (user_id, chat_id) VALUES (?, ?)', (user_id, chat_id))
                await db.commit()
                return 0, 0, 0
            return row

async def update_warns(user_id: int, chat_id: int, normal: int, heavy: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET warns_normal = ?, warns_heavy = ? WHERE user_id = ? AND chat_id = ?', 
                         (normal, heavy, user_id, chat_id))
        await db.commit()

async def add_temp_ban_count(user_id: int, chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE users 
            SET warns_normal = 0, warns_heavy = 0, temp_bans_count = temp_bans_count + 1 
            WHERE user_id = ? AND chat_id = ?
        ''', (user_id, chat_id))
        await db.commit()
        
async def reset_user(user_id: int, chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET warns_normal = 0, warns_heavy = 0, temp_bans_count = 0 WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        await db.commit()

async def get_ban_duration(chat_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT ban_time_minutes FROM settings WHERE chat_id = ?', (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 60

async def set_ban_duration(chat_id: int, minutes: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE settings SET ban_time_minutes = ? WHERE chat_id = ?', (minutes, chat_id))
        await db.commit()

# --- НОВІ ФУНКЦІЇ ДЛЯ ЛОГІВ ---
async def set_log_receiver(chat_id: int, admin_id: int):
    """Встановлює адміна, який отримуватиме логи для цього чату"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE settings SET log_receiver_id = ? WHERE chat_id = ?', (admin_id, chat_id))
        await db.commit()

async def get_log_receiver(chat_id: int):
    """Дізнаємось, кому слати логи"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT log_receiver_id FROM settings WHERE chat_id = ?', (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None
        
async def add_report(chat_id: int, message_id: int, user_id: int, reporter_id: int):
    """Створює новий репорт"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO reports (chat_id, message_id, user_id, reporter_id) 
            VALUES (?, ?, ?, ?)
        ''', (chat_id, message_id, user_id, reporter_id))
        await db.commit()

async def get_active_reports(chat_id: int):
    """Отримує список всіх репортів для конкретного чату"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row # Щоб звертатися по назвах колонок
        async with db.execute('SELECT * FROM reports WHERE chat_id = ? ORDER BY report_id ASC', (chat_id,)) as cursor:
            return await cursor.fetchall()

async def delete_report(report_id: int):
    """Видаляє репорт (коли адмін його обробив)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM reports WHERE report_id = ?', (report_id,))
        await db.commit()

async def get_reports_count(chat_id: int) -> int:
    """Рахує, скільки висить необроблених скарг"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT COUNT(*) FROM reports WHERE chat_id = ?', (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0