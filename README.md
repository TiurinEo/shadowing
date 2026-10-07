# Shadowing MVP

## Запуск (разработка)
```bash
# бэкенд
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# фронтенд (в другом терминале)
cd frontend && npm install && npm run dev
```
Открыть http://localhost:5173.

## Проверка на iPhone (нужен HTTPS для микрофона)
```bash
cloudflared tunnel --url http://localhost:5173
```
Откройте выданный https-адрес в Safari → Поделиться → На экран «Домой».

## Продакшен одним сервисом
```bash
cd frontend && npm run build
cd ../backend && uvicorn main:app --host 0.0.0.0 --port 8000
```
FastAPI сам раздаёт `frontend/dist`. Хостинг с HTTPS: Render / Fly.io / Railway.

## Что внутри
- `GET /api/transcript?url=…&lang=en` — субтитры, склеенные в предложения.
- Записи живут в памяти страницы (пропадают при перезагрузке) — IndexedDB следующим шагом.
