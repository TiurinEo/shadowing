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

## Анализ через Vocametrix
Кнопка «Проанализировать запись» под Compare запускает два независимых запроса (сбой одного не блокирует другой, у каждого есть «Повторить»):

- **Pronunciation Assessment** — `POST /api/vocametrix/pronunciation` (запись + текст фрагмента). Оценки Accuracy / Fluency / Prosody / Completeness, PronScore, слова и фонемы (фонемы — только en-US); пропущенные, неверно произнесённые и лишние слова размечаются прямо в тексте фразы. Нажмите на слово — увидите оценки звуков.
- **Prosody Similarity** — `POST /api/vocametrix/prosody` (запись + videoId + границы фрагмента). Сходство интонации, ритма, интенсивности и темпа с оригиналом.

Оценки двух сервисов **не складываются в один балл**: они измеряют разное (правильность звуков vs. сходство просодии). PronScore и «композит просодии» показываются как есть, это числа самого Vocametrix.

Настройка: в `backend/.env` задать `VOCAMETRIX_API_KEY` (ключ остаётся на сервере; остальные переменные — в `backend/.env.example`). Без ключа блок показывает «не настроен».

Ограничения: запись 1–30 с и до 10 МБ (`MAX_UPLOAD_BYTES`); лимит ключа Vocametrix — 100 запросов / 15 мин, один анализ ≈ 5 запросов (локальный счётчик отвечает 429 заранее); сетевые и 5xx-ошибки повторяются до 2 раз.

Оригинальное аудио: плеер на странице — YouTube iframe, звук недоступен браузеру, поэтому backend берёт его сам: сначала ищет `ORIGINAL_AUDIO_DIR/<videoId>.*` (можно положить файл вручную), иначе скачивает через `yt-dlp` и кэширует. Фрагмент вырезается по текущим границам фразы (с учётом подгонки). YouTube может блокировать серверные IP или требовать JS-runtime/cookies для yt-dlp (`YTDLP_COOKIES`) — тогда блок просодии покажет ошибку, а произношение продолжит работать.

Тесты: `cd backend && pip install pytest && python -m pytest tests`.
