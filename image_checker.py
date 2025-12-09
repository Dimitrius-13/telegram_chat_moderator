# image_checker.py
import asyncio
from sightengine.client import SightengineClient
import os
# Беремо ключі з сервера
API_USER = os.getenv("SIGHTENGINE_USER")
API_SECRET = os.getenv("SIGHTENGINE_SECRET")

client = SightengineClient(API_USER, API_SECRET)


client = SightengineClient(API_USER, API_SECRET)

async def check_image_content(file_path: str) -> str | None:
    """
    Відправляє фото на перевірку в AI.
    Повертає 'heavy' (оголення, зброя, насильство) або None.
    """
    try:
        # Оскільки бібліотека синхронна, запускаємо її в окремому потоці,
        # щоб не блокувати бота
        loop = asyncio.get_running_loop()
        
        # Перевіряємо на оголення (nudity), зброю (wad), образи (offensive) і gore (кров/насильство)
        output = await loop.run_in_executor(None, lambda: client.check('nudity', 'wad', 'offensive', 'gore').set_file(file_path))

        # 1. Оголення (Nudity) - ЗБАЛАНСОВАНИЙ СУВОРИЙ РЕЖИМ
        nudity = output.get('nudity', {})
        
        # 1. Повне оголення (Raw)
        # Ставимо 5% - це дуже мало, але відсіє порнографію моментально
        if nudity.get('raw', 0) > 0.05:
            return "heavy"

        # 2. Часткове оголення (Partial)
        if nudity.get('partial', 0) > 0.15: 
             return "heavy"
     
        # 3. Безпечне (Safe)
        if nudity.get('safe', 1) < 0.90:
            return "heavy"
        
        # 2. Зброя/Алкоголь/Наркотики (WAD)
        wad = output.get('weapon', 0)
        if wad > 0.8: # Якщо ймовірність зброї більше 80%
            return "heavy"

        # 3. Кров/Насильство (Gore)
        gore = output.get('gore', {}).get('prob', 0)
        if gore > 0.8:
            return "heavy"


        return None

    except Exception as e:
        print(f"Помилка перевірки зображення: {e}")
        return None
