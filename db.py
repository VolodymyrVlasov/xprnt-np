"""
Модуль роботи з базою даних SQLite.
Зберігає налаштування (key-value) та список створених ТТН.
"""
import sqlite3
from datetime import datetime

DB_PATH = "nova_ttn.db"


def get_connection():
    """Повертає з'єднання з БД з row_factory для зручного доступу до колонок."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Ініціалізує таблиці БД при першому запуску."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ttns (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ttn_number     TEXT NOT NULL,
                recipient_name TEXT,
                city           TEXT,
                warehouse      TEXT,
                raw_text       TEXT,
                created_at     TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        for _col_sql in (
            "ALTER TABLE ttns ADD COLUMN delivery_type TEXT DEFAULT 'warehouse'",
            "ALTER TABLE ttns ADD COLUMN recipient_phone TEXT",
            "ALTER TABLE ttns ADD COLUMN weight TEXT",
            "ALTER TABLE ttns ADD COLUMN dimensions TEXT",
        ):
            try:
                conn.execute(_col_sql)
            except Exception:
                pass

        # Дефолтні налаштування — INSERT OR IGNORE не перезаписує існуючі значення
        defaults = {
            "cargo_description":   "Товар",
            "cargo_weight":        "1",
            "cargo_cost":          "500",
            "cargo_seats":         "1",
            "cargo_volume_weight": "0",
            "cargo_length":        "0",
            "cargo_width":         "0",
            "cargo_height":        "0",
            "allowed_emails":      "smoloff@gmail.com,volodymyr.vlasov@gmail.com",
            "ai_system_prompt":    (
                "Ти — парсер даних отримувача для Нової Пошти.\n"
                "З довільного тексту українською або російською мовою витягни:\n"
                "1. last_name — прізвище\n"
                "2. first_name — ім'я\n"
                '3. middle_name — по-батькові (якщо є, інакше "")\n'
                "4. phone — телефон у форматі 380XXXXXXXXX (тільки цифри, без +, пробілів, дефісів)\n"
                '5. city — назва міста українською, без скорочень; якщо місто не вказано або не розпізнано — використовуй "Київ"\n'
                '6. delivery_type — тип доставки: "warehouse", "parcel_locker" або "address"\n'
                "   Правила визначення delivery_type:\n"
                '   - "відділення", "відд", номер без префіксу → "warehouse"\n'
                '   - "поштомат", "автомат", "пошт" → "parcel_locker"\n'
                '   - назва вулиці / "вул" / "пр-т" / "буд" → "address"\n'
                '   - якщо незрозуміло і є тільки число → "warehouse" (за замовчуванням)\n'
                "7. warehouse_number — тільки число (для warehouse і parcel_locker, інакше null)\n"
                "8. street — назва вулиці без слова \"вулиця/вул\" (для address, інакше null)\n"
                "9. building — номер будинку (для address, інакше null)\n"
                "10. apartment — квартира або офіс (для address, якщо є; інакше null)\n\n"
                "Відповідай ТІЛЬКИ валідним JSON без markdown, без пояснень.\n"
                "Якщо поле не вдалось визначити — значення null."
            ),
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        conn.commit()


def save_setting(key: str, value: str):
    """Зберігає або оновлює налаштування за ключем."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()


def get_setting(key: str) -> str | None:
    """Повертає значення налаштування за ключем або None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else None


def get_all_settings() -> dict:
    """Повертає всі налаштування як словник {key: value}."""
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def save_ttn(ttn_number: str, recipient_name: str, city: str,
             warehouse: str, raw_text: str, delivery_type: str = 'warehouse',
             recipient_phone: str = '', weight: str = '', dimensions: str = ''):
    """Зберігає новостворену ТТН в таблицю."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO ttns
               (ttn_number, recipient_name, city, warehouse, raw_text, delivery_type,
                recipient_phone, weight, dimensions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ttn_number, recipient_name, city, warehouse, raw_text, delivery_type,
             recipient_phone, weight, dimensions)
        )
        conn.commit()


def get_all_ttns() -> list[dict]:
    """Повертає всі ТТН відсортовані від нових до старих."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ttns ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]
