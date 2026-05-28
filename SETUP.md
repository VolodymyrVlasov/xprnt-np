# Перший запуск

## 1. Скопіювати .env.example → .env

```
cp .env.example .env
```

## 2. Отримати Google OAuth credentials

1. Відкрити [console.cloud.google.com](https://console.cloud.google.com)
2. Новий проект (або існуючий)
3. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID → Web application**
4. У полі **Authorized redirect URIs** додати:
   ```
   http://localhost:5000/login/google/authorized
   ```
5. Скопіювати **Client ID** і **Client Secret** → вставити в `.env`

## 3. Заповнити .env

```env
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your_secret
SECRET_KEY=mysecretkey123
```

## 4. Встановити залежності і запустити

```
pip install -r requirements.txt
python app.py
```

## 5. Відкрити http://localhost:5000

Авторизуватися через Google → налаштувати відправника та API ключі у ⚙ Налаштування.
