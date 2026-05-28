"""
Модуль AI-парсингу даних отримувача з довільного тексту.
Використовує claude-haiku-4-5 через офіційний Anthropic Python SDK.
"""
import json
import anthropic
import db

DEFAULT_PROMPT = """Ти — парсер даних отримувача для Нової Пошти.
З довільного тексту українською або російською мовою витягни:
1. last_name — прізвище
2. first_name — ім'я
3. middle_name — по-батькові (якщо є, інакше "")
4. phone — телефон у форматі 380XXXXXXXXX (тільки цифри, без +, пробілів, дефісів)
5. city — назва міста українською, без скорочень; якщо місто не вказано або не розпізнано — використовуй "Київ"
6. delivery_type — тип доставки: "warehouse", "parcel_locker" або "address"
   Правила визначення delivery_type:
   - "відділення", "відд", номер без префіксу → "warehouse"
   - "поштомат", "автомат", "пошт" → "parcel_locker"
   - назва вулиці / "вул" / "пр-т" / "буд" → "address"
   - якщо незрозуміло і є тільки число → "warehouse" (за замовчуванням)
7. warehouse_number — тільки число (для warehouse і parcel_locker, інакше null)
8. street — назва вулиці без слова "вулиця/вул" (для address, інакше null)
9. building — номер будинку (для address, інакше null)
10. apartment — квартира або офіс (для address, якщо є; інакше null)

Відповідай ТІЛЬКИ валідним JSON без markdown, без пояснень.
Якщо поле не вдалось визначити — значення null.

Приклади відповідей:
{"last_name":"Іваненко","first_name":"Анастасія","middle_name":"Сергіївна","phone":"380961228903","city":"Київ","delivery_type":"warehouse","warehouse_number":"79","street":null,"building":null,"apartment":null}
{"last_name":"Петренко","first_name":"Василь","middle_name":"","phone":"380671234567","city":"Харків","delivery_type":"parcel_locker","warehouse_number":"1234","street":null,"building":null,"apartment":null}
{"last_name":"Коваль","first_name":"Марія","middle_name":"","phone":"380501112233","city":"Одеса","delivery_type":"address","warehouse_number":null,"street":"Дерибасівська","building":"10","apartment":"5"}"""

# Обов'язкові поля для всіх типів доставки
REQUIRED_FIELDS_COMMON = ["last_name", "first_name", "phone", "city"]

# Додаткові обов'язкові поля залежно від типу доставки
REQUIRED_FIELDS_BY_TYPE = {
    "warehouse":     ["warehouse_number"],
    "parcel_locker": ["warehouse_number"],
    "address":       ["street", "building"],
}


def parse_recipient(text: str, claude_api_key: str) -> dict:
    """
    Парсує дані отримувача з довільного тексту за допомогою Claude.

    Повертає словник із полями: last_name, first_name, middle_name,
    phone, city, warehouse_number.

    Викидає ValueError якщо будь-яке обов'язкове поле не розпізнано.
    """
    # Промпт береться з налаштувань; DEFAULT_PROMPT — резервний варіант
    system_prompt = db.get_setting("ai_system_prompt") or DEFAULT_PROMPT

    client = anthropic.Anthropic(api_key=claude_api_key)

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system=system_prompt,
        messages=[
            {"role": "user", "content": text}
        ]
    )

    raw_response = message.content[0].text.strip()

    # Видаляємо можливі markdown-блоки якщо модель все ж їх додала
    if raw_response.startswith("```"):
        lines = raw_response.split("\n")
        raw_response = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI повернув некоректний JSON: {e}. Відповідь: {raw_response}")

    # Нормалізуємо delivery_type (за замовчуванням warehouse)
    delivery_type = parsed.get("delivery_type") or "warehouse"
    if delivery_type not in ("warehouse", "parcel_locker", "address"):
        delivery_type = "warehouse"
    parsed["delivery_type"] = delivery_type

    # Якщо AI не розпізнав місто — підставляємо Київ до перевірки обов'язкових полів
    if not parsed.get("city"):
        parsed["city"] = "Київ"

    # Перевіряємо обов'язкові поля (загальні + специфічні для типу доставки)
    required = REQUIRED_FIELDS_COMMON + REQUIRED_FIELDS_BY_TYPE.get(delivery_type, [])
    missing = [f for f in required if not parsed.get(f)]
    if missing:
        field_names = {
            "last_name":        "Прізвище",
            "first_name":       "Ім'я",
            "phone":            "Телефон",
            "city":             "Місто",
            "warehouse_number": "Номер відділення/поштомату",
            "street":           "Вулиця",
            "building":         "Номер будинку",
        }
        missing_ua = [field_names.get(f, f) for f in missing]
        raise ValueError(f"Не вдалось розпізнати: {', '.join(missing_ua)}")

    # Гарантуємо, що необов'язкові поля є рядками, а не null
    for optional in ("middle_name", "apartment"):
        if parsed.get(optional) is None:
            parsed[optional] = ""

    return parsed
