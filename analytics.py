import matplotlib.pyplot as plt
import io

def create_chart(data: list, title: str) -> io.BytesIO:
    """
    Малює графік: data = [(user_id, count), ...]
    Повертає байт-об'єкт картинки.
    """
    if not data:
        return None

    # Скорочуємо ID до останніх 4 цифр для краси, або беремо імена якщо є
    users = [f"..{str(x[0])[-4:]}" for x in data] 
    counts = [x[1] for x in data]

    plt.figure(figsize=(8, 5))
    # Малюємо стовпчики
    bars = plt.bar(users, counts, color='#6c5ce7') # Фіолетовий колір

    plt.xlabel('Користувачі (ID)')
    plt.ylabel('Повідомлення')
    plt.title(title)
    plt.grid(axis='y', linestyle='--', alpha=0.5)

    # Цифри над стовпчиками
    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height,
                 f'{int(height)}',
                 ha='center', va='bottom')

    # Зберігаємо в буфер пам'яті
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    
    return buf
