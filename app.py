"""
Головний Flask-застосунок для створення ТТН Нової Пошти.
Запуск: python app.py
Відкрити: http://localhost:5000
"""
import os
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from functools import wraps
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_dance.contrib.google import make_google_blueprint, google

import db
import ai_parser
import np_api

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

db.init_db()

google_bp = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
    redirect_to="index",
)
app.register_blueprint(google_bp, url_prefix="/login")

from flask_dance.consumer import oauth_authorized

@oauth_authorized.connect_via(google_bp)
def google_logged_in(blueprint, token):
    if not token:
        return False
    resp = blueprint.session.get("/oauth2/v2/userinfo")
    if resp.ok:
        info = resp.json()
        session["user_email"] = info.get("email", "")
        session["user_name"]  = info.get("name", "")
        session["logged_in"]  = True
    return False


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        email = session.get("user_email", "")
        allowed_raw = db.get_setting("allowed_emails") or ""
        allowed = [e.strip() for e in allowed_raw.split(",") if e.strip()]
        if allowed and email not in allowed:
            return render_template("access_denied.html", email=email)
        return f(*args, **kwargs)
    return decorated


@app.route("/login")
def login_page():
    """Сторінка входу."""
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    return render_template("index.html", user_email=session.get("user_email", ""))


@app.route("/settings", methods=["GET"])
@login_required
def get_settings():
    """Повертає поточні налаштування як JSON. Чутливі поля маскуються."""
    settings = db.get_all_settings()
    settings.pop("flask_secret_key", None)
    for secret_key in ("np_api_key", "claude_api_key"):
        if settings.get(secret_key):
            settings[secret_key] = "***"
    return jsonify(settings)


@app.route("/settings/save", methods=["POST"])
@login_required
def save_settings():
    """Зберігає налаштування. Не перезаписує API-ключі якщо передано '***'."""
    data = request.get_json() or request.form.to_dict()
    for key, value in data.items():
        if key == "flask_secret_key":
            continue
        if value == "***":
            continue
        if value is not None:
            db.save_setting(key, str(value))
    return jsonify({"success": True})


def _cargo_dims(cargo_settings: dict, delivery_type: str) -> tuple[str, str]:
    """Повертає (actual_weight, dimensions_str) для збереження в БД."""
    actual_weight = cargo_settings.get("cargo_weight") or "1"
    l = int(cargo_settings.get("cargo_length") or 0)
    w = int(cargo_settings.get("cargo_width")  or 0)
    h = int(cargo_settings.get("cargo_height") or 0)
    if delivery_type == "parcel_locker":
        l = l or 10
        w = w or 10
        h = h or 10
    dims = f"{l}x{w}x{h}" if (l or w or h) else ""
    return actual_weight, dims


@app.route("/create", methods=["POST"])
@login_required
def create_ttn():
    """Парсить текст через AI, знаходить дані в НП, створює ТТН."""
    body = request.get_json() or {}
    raw_text = (body.get("text") or "").strip()

    if not raw_text:
        return jsonify({"success": False, "error": "Введіть текст для розпізнавання"})

    settings = db.get_all_settings()
    np_key     = settings.get("np_api_key")
    claude_key = settings.get("claude_api_key")

    required_settings = {
        "np_api_key":      "API ключ Нової Пошти",
        "claude_api_key":  "API ключ Anthropic",
        "sender_phone":    "Телефон відправника",
        "sender_city":     "Місто відправника",
        "sender_warehouse":"Відділення відправника",
        "sender_np_ref":   "Ref відправника в НП",
    }
    missing = [label for key, label in required_settings.items()
               if not settings.get(key)]
    if missing:
        return jsonify({
            "success": False,
            "error": f"Заповніть налаштування відправника та API ключі: {', '.join(missing)}"
        })

    # Крок 1: AI-парсинг
    try:
        parsed = ai_parser.parse_recipient(raw_text, claude_key)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e),
                        "step": "ai_parse", "stage": "parse"})
    except Exception as e:
        return jsonify({"success": False,
                        "error": f"Помилка AI-парсингу: {e}",
                        "step": "ai_parse", "stage": "parse"})

    if not parsed.get("city"):
        parsed["city"] = "Київ"

    if body.get("preview_only"):
        return jsonify({"success": True, "parsed": parsed})

    # Крок 2: Дані відправника
    try:
        sender_city_ref = np_api.find_city_ref(np_key, settings["sender_city"])
        sender_warehouse_ref = np_api.find_warehouse_ref(
            np_key, sender_city_ref, settings["sender_warehouse"])
        sender_contact_ref = np_api.get_sender_contact_ref(
            np_key, settings["sender_np_ref"])
    except ValueError as e:
        return jsonify({"success": False,
                        "error": f"Помилка даних відправника: {e}",
                        "step": "sender", "stage": "create"})

    # Крок 3: Дані отримувача
    delivery_type = parsed.get("delivery_type", "warehouse")
    try:
        recipient_city_ref = np_api.find_city_ref(np_key, parsed["city"])

        if delivery_type == "parcel_locker":
            recipient_address_ref = np_api.find_parcel_locker_ref(
                np_key, recipient_city_ref, parsed["warehouse_number"])
        elif delivery_type == "address":
            street_ref = np_api.find_street_ref(
                np_key, recipient_city_ref, parsed["street"])
            recipient_address_ref = np_api.create_address_ref(
                np_key, street_ref, parsed["building"], parsed.get("apartment", ""))
        else:
            recipient_address_ref = np_api.find_warehouse_ref(
                np_key, recipient_city_ref, parsed["warehouse_number"])

        recipient_counterparty_ref, recipient_contact_ref = np_api.create_recipient(
            np_key,
            parsed["last_name"],
            parsed["first_name"],
            parsed.get("middle_name", ""),
            parsed["phone"],
            recipient_city_ref,
        )
    except ValueError as e:
        return jsonify({"success": False,
                        "error": f"Помилка даних отримувача: {e}",
                        "step": "recipient", "stage": "create"})

    # Крок 4: Створюємо ТТН
    sender_info = {
        "city_ref":      sender_city_ref,
        "sender_np_ref": settings["sender_np_ref"],
        "warehouse_ref": sender_warehouse_ref,
        "contact_ref":   sender_contact_ref,
        "phone":         settings["sender_phone"],
    }
    recipient_info = {
        "city_ref":         recipient_city_ref,
        "counterparty_ref": recipient_counterparty_ref,
        "warehouse_ref":    recipient_address_ref,
        "contact_ref":      recipient_contact_ref,
        "phone":            parsed["phone"],
    }

    cargo_settings = {k: settings.get(k) or v for k, v in {
        "cargo_description":   "Товар",
        "cargo_weight":        "1",
        "cargo_cost":          "500",
        "cargo_seats":         "1",
        "cargo_length":        "0",
        "cargo_width":         "0",
        "cargo_height":        "0",
        "cargo_volume_weight": "0",
    }.items()}

    try:
        ttn_number = np_api.create_ttn(
            np_key, sender_info, recipient_info, delivery_type, cargo_settings)
    except ValueError as e:
        return jsonify({"success": False,
                        "error": f"Помилка створення ТТН: {e}",
                        "step": "create_ttn", "stage": "create"})

    # Крок 5: Зберігаємо в БД
    recipient_name = " ".join(filter(None, [
        parsed["last_name"], parsed["first_name"], parsed.get("middle_name", "")
    ]))
    if delivery_type == "address":
        addr_parts = [parsed.get("street", ""), parsed.get("building", "")]
        if parsed.get("apartment"):
            addr_parts.append(f"кв. {parsed['apartment']}")
        warehouse_display = ", ".join(filter(None, addr_parts))
    else:
        warehouse_display = parsed.get("warehouse_number", "")

    actual_weight, dims = _cargo_dims(cargo_settings, delivery_type)

    db.save_ttn(
        ttn_number=ttn_number,
        recipient_name=recipient_name,
        city=parsed["city"],
        warehouse=warehouse_display,
        raw_text=raw_text,
        delivery_type=delivery_type,
        recipient_phone=parsed["phone"],
        weight=actual_weight,
        dimensions=dims,
    )

    return jsonify({
        "success":    True,
        "ttn_number": ttn_number,
        "ttn":        ttn_number,
        "parsed":     parsed,
    })


@app.route("/ttns", methods=["GET"])
@login_required
def get_ttns():
    return jsonify(db.get_all_ttns())


@app.route("/repeat/preview", methods=["POST"])
@login_required
def repeat_preview():
    body = request.get_json() or {}
    ttn_number = (body.get("ttn_number") or "").strip()
    if not ttn_number:
        return jsonify({"success": False, "error": "Введіть номер ТТН"})

    settings = db.get_all_settings()
    np_key = settings.get("np_api_key")
    if not np_key:
        return jsonify({"success": False,
                        "error": "Заповніть API ключ Нової Пошти в налаштуваннях"})

    try:
        data = np_api.get_ttn_data(np_key, ttn_number)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)})

    return jsonify({"success": True, "data": data})


@app.route("/repeat/create", methods=["POST"])
@login_required
def repeat_create():
    body = request.get_json() or {}
    ttn_number = (body.get("ttn_number") or "").strip()
    if not ttn_number:
        return jsonify({"success": False, "error": "Введіть номер ТТН"})

    settings = db.get_all_settings()

    required_settings = {
        "np_api_key":       "API ключ Нової Пошти",
        "sender_phone":     "Телефон відправника",
        "sender_city":      "Місто відправника",
        "sender_warehouse": "Відділення відправника",
        "sender_np_ref":    "Ref відправника в НП",
    }
    missing = [label for key, label in required_settings.items()
               if not settings.get(key)]
    if missing:
        return jsonify({"success": False,
                        "error": f"Заповніть налаштування: {', '.join(missing)}"})

    np_key = settings["np_api_key"]

    try:
        old = np_api.get_ttn_data(np_key, ttn_number)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)})

    for field, label in [("city_ref", "Ref міста"),
                         ("counterparty_ref", "Ref контрагента"),
                         ("warehouse_ref", "Ref відділення")]:
        if not old.get(field):
            return jsonify({"success": False,
                            "error": f"Не вдалось отримати {label} зі старої ТТН"})
    if not old.get("recipient_phone"):
        return jsonify({"success": False,
                        "error": "Не вдалось отримати телефон з ТТН. "
                                 "Перевірте що ТТН належить вашому акаунту НП."})

    try:
        sender_city_ref = np_api.find_city_ref(np_key, settings["sender_city"])
        sender_warehouse_ref = np_api.find_warehouse_ref(
            np_key, sender_city_ref, settings["sender_warehouse"])
        sender_contact_ref = np_api.get_sender_contact_ref(
            np_key, settings["sender_np_ref"])
    except ValueError as e:
        return jsonify({"success": False,
                        "error": f"Помилка даних відправника: {e}"})

    sender_info = {
        "city_ref":      sender_city_ref,
        "sender_np_ref": settings["sender_np_ref"],
        "warehouse_ref": sender_warehouse_ref,
        "contact_ref":   sender_contact_ref,
        "phone":         settings["sender_phone"],
    }

    counterparty_ref = old["counterparty_ref"]
    contact_ref = old.get("contact_ref") or ""
    if not contact_ref:
        name_parts = (old.get("recipient_name") or "").split()
        try:
            counterparty_ref, contact_ref = np_api.create_recipient(
                np_key,
                last_name=name_parts[0]  if len(name_parts) > 0 else "",
                first_name=name_parts[1] if len(name_parts) > 1 else "",
                middle_name=name_parts[2] if len(name_parts) > 2 else "",
                phone=old["recipient_phone"],
                city_ref=old["city_ref"],
            )
        except ValueError as e:
            return jsonify({"success": False,
                            "error": f"Помилка створення контрагента: {e}"})

    recipient_info = {
        "city_ref":         old["city_ref"],
        "counterparty_ref": counterparty_ref,
        "warehouse_ref":    old["warehouse_ref"],
        "contact_ref":      contact_ref,
        "phone":            old["recipient_phone"],
    }

    delivery_type = old["delivery_type"]

    cargo_settings = {k: settings.get(k) or v for k, v in {
        "cargo_description":   "Товар",
        "cargo_weight":        "1",
        "cargo_cost":          "500",
        "cargo_seats":         "1",
        "cargo_length":        "0",
        "cargo_width":         "0",
        "cargo_height":        "0",
        "cargo_volume_weight": "0",
    }.items()}

    try:
        new_ttn = np_api.create_ttn(
            np_key, sender_info, recipient_info, delivery_type, cargo_settings)
    except ValueError as e:
        return jsonify({"success": False,
                        "error": f"Помилка створення ТТН: {e}"})

    warehouse_display = (
        old.get("warehouse_description", "") if delivery_type == "address"
        else old.get("warehouse_number", "")
    )

    actual_weight, dims = _cargo_dims(cargo_settings, delivery_type)

    db.save_ttn(
        ttn_number=new_ttn,
        recipient_name=old["recipient_name"],
        city=old["city"],
        warehouse=warehouse_display,
        raw_text=f"Повторна ТТН на основі {ttn_number}",
        delivery_type=delivery_type,
        recipient_phone=old["recipient_phone"],
        weight=actual_weight,
        dimensions=dims,
    )

    return jsonify({"success": True, "ttn": new_ttn})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
