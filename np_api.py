"""
Модуль взаємодії з REST API Нової Пошти v2.0.
Документація: https://developers.novaposhta.ua/
"""
import re
import requests

NP_API_URL = "https://api.novaposhta.ua/v2.0/json/"


def _post(api_key: str, model_name: str, called_method: str,
          method_properties: dict) -> list:
    """
    Базовий POST-запит до API НП.
    Повертає список data зі відповіді або кидає ValueError при помилці.
    """
    payload = {
        "apiKey": api_key,
        "modelName": model_name,
        "calledMethod": called_method,
        "methodProperties": method_properties,
    }
    try:
        resp = requests.post(NP_API_URL, json=payload, timeout=15)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        raise ValueError(f"Помилка з'єднання з API НП: {e}")

    if not body.get("success"):
        errors = body.get("errors") or body.get("errorCodes") or ["Невідома помилка НП"]
        raise ValueError(f"API НП повернув помилку: {'; '.join(str(e) for e in errors)}")

    return body.get("data", [])


def find_city_ref(api_key: str, city_name: str) -> str:
    """Знаходить Ref міста за його назвою. Повертає Ref першого збігу."""
    data = _post(api_key, "Address", "getCities",
                 {"FindByString": city_name})
    if not data:
        raise ValueError(f"Місто '{city_name}' не знайдено в базі НП")
    return data[0]["Ref"]


def find_warehouse_ref(api_key: str, city_ref: str, warehouse_number: str) -> str:
    """Знаходить Ref відділення НП за Ref міста та номером відділення."""
    data = _post(api_key, "Address", "getWarehouses",
                 {"CityRef": city_ref, "WarehouseId": warehouse_number})
    if not data:
        raise ValueError(
            f"Відділення №{warehouse_number} не знайдено для вказаного міста"
        )
    return data[0]["Ref"]


def find_parcel_locker_ref(api_key: str, city_ref: str, locker_number: str) -> str:
    """Знаходить Ref поштомату НП за Ref міста та номером поштомату."""
    # TypeOfWarehouseRef — фіксований Ref типу "Поштомат" в системі НП
    data = _post(api_key, "Address", "getWarehouses", {
        "CityRef": city_ref,
        "WarehouseId": locker_number,
        "TypeOfWarehouseRef": "f9316480-5f2d-425d-bc2c-ac7cd29decf0",
    })
    if not data:
        raise ValueError(
            f"Поштомат №{locker_number} не знайдено для вказаного міста"
        )
    return data[0]["Ref"]


def find_street_ref(api_key: str, city_ref: str, street_name: str) -> str:
    """Знаходить Ref вулиці за Ref міста та назвою вулиці."""
    data = _post(api_key, "Address", "getStreet",
                 {"CityRef": city_ref, "FindByString": street_name})
    if not data:
        raise ValueError(f"Вулиця '{street_name}' не знайдена для вказаного міста")
    return data[0]["Ref"]


def create_address_ref(api_key: str, street_ref: str,
                       building: str, apartment: str = "") -> str:
    """Створює адресу в системі НП і повертає її Ref."""
    data = _post(api_key, "Address", "save", {
        "StreetRef":     street_ref,
        "BuildingNumber": building,
        "Flat":          apartment or "",
    })
    if not data:
        raise ValueError("Не вдалось створити адресу в НП")
    return data[0]["Ref"]


def create_recipient(api_key: str, last_name: str, first_name: str,
                     middle_name: str, phone: str, city_ref: str) -> tuple:
    """
    Створює контрагента-отримувача і повертає (counterparty_ref, contact_ref).
    НП може повернути "Person already exists!" у warnings — це нормально,
    Ref і contact_ref у відповіді завжди коректні.
    """
    data = _post(api_key, "Counterparty", "save", {
        "FirstName":            first_name,
        "LastName":             last_name,
        "MiddleName":           middle_name or "",
        "Phone":                phone,
        "CityRef":              city_ref,
        "CounterpartyType":     "PrivatePerson",
        "CounterpartyProperty": "Recipient",
    })
    if not data:
        raise ValueError("Не вдалось створити контрагента в НП")
    counterparty_ref = data[0]["Ref"]
    contact_ref      = data[0]["ContactPerson"]["data"][0]["Ref"]
    return counterparty_ref, contact_ref


def get_sender_contact_ref(api_key: str, sender_ref: str) -> str:
    """Повертає Ref контактної особи відправника."""
    data = _post(api_key, "Counterparty", "getCounterpartyContactPersons",
                 {"Ref": sender_ref})
    if not data:
        raise ValueError("Не знайдено контактних осіб для відправника")
    return data[0]["Ref"]


def create_ttn(api_key: str, sender_settings: dict, recipient_data: dict,
               delivery_type: str = "warehouse",
               cargo_settings: dict = None) -> str:
    """
    Створює ТТН (інтернет-документ) в системі НП.

    sender_settings — словник з полями:
        city_ref, sender_np_ref, warehouse_ref, contact_ref, phone

    recipient_data — словник з полями:
        city_ref, counterparty_ref, warehouse_ref, contact_ref, phone

    delivery_type — "warehouse" | "parcel_locker" | "address"

    cargo_settings — необов'язковий словник з параметрами вантажу
        (cargo_description, cargo_weight, cargo_cost, cargo_seats,
         cargo_length, cargo_width, cargo_height, cargo_volume_weight)

    Повертає номер ТТН (IntDocNumber).
    """
    cargo_settings = cargo_settings or {}
    # Адресна доставка використовує ServiceType "WarehouseDoorsD"
    service_type = "WarehouseDoorsD" if delivery_type == "address" else "WarehouseWarehouse"

    props = {
        "PayerType":     "Recipient",
        "PaymentMethod": "Cash",
        "CargoType":     "Parcel",
        "ServiceType":   service_type,
        "Description":   cargo_settings.get("cargo_description") or "Товар",
        "Weight":        cargo_settings.get("cargo_weight")      or "1",
        "Cost":          cargo_settings.get("cargo_cost")        or "500",
        # Відправник
        "CitySender":    sender_settings["city_ref"],
        "Sender":        sender_settings["sender_np_ref"],
        "SenderAddress": sender_settings["warehouse_ref"],
        "ContactSender": sender_settings["contact_ref"],
        "SendersPhone":  sender_settings["phone"],
        # Отримувач
        "CityRecipient":    recipient_data["city_ref"],
        "Recipient":        recipient_data["counterparty_ref"],
        "RecipientAddress": recipient_data["warehouse_ref"],
        "ContactRecipient": recipient_data["contact_ref"],
        "RecipientsPhone":  recipient_data["phone"],
    }

    seats_amount = int(cargo_settings.get("cargo_seats") or 1)
    weight = str(cargo_settings.get("cargo_weight") or 1)

    props["SeatsAmount"] = str(seats_amount)

    # OptionsSeat обов'язковий для поштомату, для відділення — не передавати
    if delivery_type == "parcel_locker":
        # or 10 підставляє мінімум 10 см якщо поле порожнє або 0
        length = int(cargo_settings.get("cargo_length") or 0) or 10
        width  = int(cargo_settings.get("cargo_width")  or 0) or 10
        height = int(cargo_settings.get("cargo_height") or 0) or 10
        seat = {
            "weight":           weight,
            "volumetricWeight": str(cargo_settings.get("cargo_volume_weight") or 0),
            "volumetricLength": str(length),
            "volumetricWidth":  str(width),
            "volumetricHeight": str(height),
        }
        props["OptionsSeat"] = [seat] * seats_amount

    data = _post(api_key, "InternetDocument", "save", props)
    if not data:
        raise ValueError("НП не повернула дані про створену ТТН")
    return data[0]["IntDocNumber"]


def get_ttn_data(api_key: str, ttn_number: str) -> dict:
    """
    Отримує дані ТТН за її номером для повторного використання.
    Повертає словник з refs і описовими полями отримувача.
    """
    data = _post(api_key, "InternetDocument", "getDocumentList", {
        "IntDocNumber": ttn_number,
        "GetFullList": "1",
    })
    if not data:
        raise ValueError("ТТН не знайдено або належить іншому відправнику")

    doc = data[0]
    service_type = doc.get("ServiceType", "")

    # Текстовий опис відділення/адреси — RecipientAddressDescription є завжди,
    # WarehouseRecipient у getDocumentList повертається null
    warehouse_desc = (
        doc.get("RecipientAddressDescription")
        or doc.get("WarehouseRecipientDescription")
        or ""
    )

    # Визначаємо delivery_type
    if service_type == "WarehouseDoorsD":
        delivery_type = "address"
    elif "поштомат" in warehouse_desc.lower():
        delivery_type = "parcel_locker"
    else:
        delivery_type = "warehouse"

    # Номер відділення — беремо з вкладеного SettlmentAddressData або regex з опису
    settlement = doc.get("SettlmentAddressData") or {}
    warehouse_number = settlement.get("RecipientWarehouseNumber") or ""
    if not warehouse_number and delivery_type in ("warehouse", "parcel_locker"):
        m = re.search(r"\d+", warehouse_desc)
        warehouse_number = m.group(0) if m else ""

    # RecipientFullName буває null (наприклад, у доставлених посилок) —
    # замість нього використовуємо RecipientContactPerson або RecipientDescription
    recipient_name = (
        doc.get("RecipientContactPerson")
        or doc.get("RecipientDescription")
        or doc.get("RecipientFullName")
        or ""
    )

    # Телефон: RecipientsPhone (з 's') — правильна назва поля в getDocumentList
    recipient_phone = (
        doc.get("RecipientsPhone")
        or doc.get("RecipientContactPhone")
        or doc.get("PhoneRecipient")
        or ""
    )

    return {
        "recipient_name":        recipient_name,
        "recipient_phone":       recipient_phone,
        "city":                  doc.get("CityRecipientDescription", ""),
        "city_ref":              doc.get("CityRecipient", ""),
        "warehouse_number":      warehouse_number,
        "warehouse_ref":         doc.get("RecipientAddress", ""),
        "warehouse_description": warehouse_desc,
        "counterparty_ref":      doc.get("Recipient", ""),
        "contact_ref":           doc.get("ContactRecipient", ""),
        "service_type":          service_type,
        "delivery_type":         delivery_type,
    }
