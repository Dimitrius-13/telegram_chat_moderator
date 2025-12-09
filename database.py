import asyncpg
import os

# Отримуємо URL бази з серверних змінних (або встав сюди свій рядок для тесту)
# Наприклад: "postgres://user:pass@host/db"
DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    print("❌ CRITICAL ERROR: DATABASE_URL is missing!")

pool = None

async def init_db():
    global pool
    # Створюємо пул з'єднань (це набагато швидше, ніж відкривати файл щоразу)
    pool = await asyncpg.create_pool(dsn=DB_URL)
    
    async with pool.acquire() as conn:
        # Таблиця юзерів
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT,
                chat_id BIGINT,
                warns_normal INTEGER DEFAULT 0,
                warns_heavy INTEGER DEFAULT 0,
                temp_bans_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        
        # Таблиця налаштувань
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                chat_id BIGINT PRIMARY KEY,
                chat_title TEXT,
                ban_time_minutes INTEGER DEFAULT 60,
                log_receiver_id BIGINT DEFAULT NULL
            )
        ''')
        
        # Таблиця репортів (SERIAL замість AUTOINCREMENT)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                report_id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                message_id BIGINT,
                user_id BIGINT,
                reporter_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

async def update_chat_title(chat_id: int, title: str):
    # У Postgres замість INSERT OR IGNORE використовують ON CONFLICT
    await pool.execute('''
        INSERT INTO settings (chat_id, chat_title) VALUES ($1, $2)
        ON CONFLICT (chat_id) DO NOTHING
    ''', chat_id, title)
    
    await pool.execute('UPDATE settings SET chat_title = $1 WHERE chat_id = $2', title, chat_id)

async def get_all_chats():
    rows = await pool.fetch('SELECT chat_id, chat_title FROM settings')
    return rows # asyncpg повертає об'єкти, схожі на словники, це ок для твого коду

async def get_user_stats(user_id: int, chat_id: int):
    # Спочатку пробуємо отримати дані
    row = await pool.fetchrow('SELECT warns_normal, warns_heavy, temp_bans_count FROM users WHERE user_id = $1 AND chat_id = $2', user_id, chat_id)
    
    if not row:
        # Якщо немає - створюємо. 
        # Використовуємо ON CONFLICT DO NOTHING, щоб уникнути помилок при паралельних запитах
        await pool.execute('''
            INSERT INTO users (user_id, chat_id) 
            VALUES ($1, $2) 
            ON CONFLICT (user_id, chat_id) DO NOTHING
        ''', user_id, chat_id)
        return 0, 0, 0
        
    return row['warns_normal'], row['warns_heavy'], row['temp_bans_count']

async def update_warns(user_id: int, chat_id: int, normal: int, heavy: int):
    await pool.execute('UPDATE users SET warns_normal = $1, warns_heavy = $2 WHERE user_id = $3 AND chat_id = $4', 
                       normal, heavy, user_id, chat_id)

async def add_temp_ban_count(user_id: int, chat_id: int):
    await pool.execute('''
        UPDATE users 
        SET warns_normal = 0, warns_heavy = 0, temp_bans_count = temp_bans_count + 1 
        WHERE user_id = $1 AND chat_id = $2
    ''', user_id, chat_id)
        
async def reset_user(user_id: int, chat_id: int):
    await pool.execute('UPDATE users SET warns_normal = 0, warns_heavy = 0, temp_bans_count = 0 WHERE user_id = $1 AND chat_id = $2', user_id, chat_id)

async def get_ban_duration(chat_id: int) -> int:
    row = await pool.fetchrow('SELECT ban_time_minutes FROM settings WHERE chat_id = $1', chat_id)
    return row['ban_time_minutes'] if row else 60

async def set_ban_duration(chat_id: int, minutes: int):
    await pool.execute('UPDATE settings SET ban_time_minutes = $1 WHERE chat_id = $2', minutes, chat_id)

# --- ЛОГИ ---
async def set_log_receiver(chat_id: int, admin_id: int):
    await pool.execute('UPDATE settings SET log_receiver_id = $1 WHERE chat_id = $2', admin_id, chat_id)

async def get_log_receiver(chat_id: int):
    row = await pool.fetchrow('SELECT log_receiver_id FROM settings WHERE chat_id = $1', chat_id)
    # У asyncpg перевіряємо, чи є рядок і чи поле не None
    return row['log_receiver_id'] if row and row['log_receiver_id'] is not None else None
        
async def add_report(chat_id: int, message_id: int, user_id: int, reporter_id: int):
    await pool.execute('''
        INSERT INTO reports (chat_id, message_id, user_id, reporter_id) 
        VALUES ($1, $2, $3, $4)
    ''', chat_id, message_id, user_id, reporter_id)

async def get_active_reports(chat_id: int):
    # asyncpg повертає список Record, які працюють як dict. 
    # Тобто report['user_id'] з твого main.py працюватиме без змін.
    return await pool.fetch('SELECT * FROM reports WHERE chat_id = $1 ORDER BY report_id ASC', chat_id)

async def delete_report(report_id: int):
    await pool.execute('DELETE FROM reports WHERE report_id = $1', report_id)

async def get_reports_count(chat_id: int) -> int:
    # COUNT(*) повертає число
    val = await pool.fetchval('SELECT COUNT(*) FROM reports WHERE chat_id = $1', chat_id)
    return val if val else 0
