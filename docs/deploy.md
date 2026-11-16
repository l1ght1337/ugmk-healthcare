# Бот на сервере

Бот работает в Docker на отдельной ВМ (Ubuntu 26.04, 1 ядро, 2 ГБ памяти). Адрес ВМ в git не хранится: он лежит в локальном файле `.deploy.local` в корне репозитория (образец — `scripts/deploy.local.example`). Ниже `$DEPLOY_HOST` — значение из этого файла, вида `deploy@<адрес>`.

Чтобы команды ниже работали как написано, один раз в терминале: `source .deploy.local`.

## Что где лежит

| Что | Где |
|---|---|
| Проект на ВМ | `/opt/ugmk-bot`, владелец — пользователь `deploy` (uid 1000, группа `docker`) |
| Код | `app`, `config`, `kb/scenarios`, `kb/info`, `assets`, `pyproject.toml`, `uv.lock`, `.python-version`, `Dockerfile`, `.dockerignore`, `docker-compose.yml` |
| Чего на ВМ нет | `kb/text`, `kb/source_pdf`, `docs`, `tests`, `.git` |
| Секреты | `/opt/ugmk-bot/.env`, права 600. В git и в образ не попадает |
| Данные | `/opt/ugmk-bot/data` — том контейнера `/app/data` |
| Какой коммит развёрнут | `/opt/ugmk-bot/REVISION` |
| Контейнер | `ugmk-bot`, образ `ugmk-bot:latest` (и `ugmk-bot:<коммит>` для каждого развёртывания), работает под uid 1000 |

В `data` лежат:

- `bot.db` — база: пользователи, пациенты, записи, напоминания, обращения;
- `schedule.csv` — демо-расписание, бот строит и дополняет его сам;
- `llm_usage.jsonl` — учёт токенов DeepSeek, `misses.jsonl` — журнал непонятого;
- `media_ids.json` — `file_id` роликов и фото, которые бот уже загрузил в Telegram (привязаны к токену бота).

Портов наружу контейнер не открывает: бот ходит в Telegram сам (long polling). Root нужен только для установки пакетов; всё остальное делается под `deploy`.

## Обновить

1. Закоммитить изменения: на сервер уходит коммит, а не рабочий каталог.
2. Запустить из корня репозитория:

   ```bash
   scripts/deploy.sh
   ```

Скрипт отправляет архив `HEAD` по ssh (`git archive`, ключей GitHub на ВМ нет), заменяет код целиком, собирает образ, перезапускает контейнер и показывает последние 30 строк лога. `.env` и `data` он не трогает. Бот при этом недоступен несколько секунд; напоминания хранятся в базе и не теряются.

Изменился только `.env` — пересоздать контейнер без сборки:

```bash
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose up -d --force-recreate'
```

## Скопировать `.env`

Из корня репозитория на Mac. Файл создаётся сразу с правами 600:

```bash
ssh "$DEPLOY_HOST" 'umask 077 && cat > /opt/ugmk-bot/.env' < .env
ssh "$DEPLOY_HOST" 'chmod 600 /opt/ugmk-bot/.env && ls -l /opt/ugmk-bot/.env'
```

Пути в `.env` должны быть относительными, как в `.env.example` (`data/bot.db`, `data/llm_usage.jsonl`): в контейнере проект лежит в `/app`.

**Два бота с одним токеном одновременно не работают**: Telegram отдаёт сообщения только одному, второй получает ошибку `Conflict`. Перед запуском на ВМ локальный бот нужно остановить — и наоборот.

## Логи и состояние

```bash
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose ps'
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose logs --tail 100 bot'
ssh -t "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose logs -f --tail 50 bot'   # следить, выход — Ctrl+C
ssh "$DEPLOY_HOST" 'cat /opt/ugmk-bot/REVISION'
```

Логи контейнера ограничены: 5 файлов по 10 МБ. Время в логах и в контейнере — Екатеринбург (`TZ=Asia/Yekaterinburg`), на самой ВМ — UTC.

Остановить и запустить:

```bash
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose stop'
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose up -d'
```

После перезагрузки ВМ контейнер поднимается сам (`restart: unless-stopped`), если его не останавливали командой `stop`.

## Откатиться

Развернуть прежний коммит тем же скриптом:

```bash
git log --oneline -10            # найти коммит, который работал
scripts/deploy.sh <коммит>
```

Слои образа закэшированы, сборка занимает меньше минуты. База при откате остаётся как есть: если новая версия успела записать в неё что-то, чего старая не понимает, перед откатом сохраните `data/bot.db`.

## Сохранить данные

```bash
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose stop'
scp "$DEPLOY_HOST":/opt/ugmk-bot/data/bot.db "bot.$(date +%Y%m%d).db"
ssh "$DEPLOY_HOST" 'cd /opt/ugmk-bot && docker compose up -d'
```

В `bot.db` — персональные данные пациентов: копию не коммитить и не пересылать.

## Если что-то не так

- **Контейнер перезапускается по кругу.** `docker compose logs --tail 50 bot`: чаще всего не заполнен `.env` (`TELEGRAM_BOT_TOKEN`, `ADMIN_CHAT_ID`) или тот же токен запущен где-то ещё (`Conflict`).
- **Нет доступа к `data`.** Каталог должен принадлежать uid 1000: `ls -ld /opt/ugmk-bot/data`.
- **Сборка падает по памяти.** На ВМ 2 ГБ и нет swap. Первая сборка прошла за полминуты без него; если понадобится — файл подкачки на 1 ГБ (под root).
- **Сеть.** С ВМ должны отвечать `https://api.telegram.org` и `https://api.deepseek.com`:

  ```bash
  ssh "$DEPLOY_HOST" 'curl -sS -m 10 -o /dev/null -w "%{http_code}\n" https://api.telegram.org; curl -sS -m 10 -o /dev/null -w "%{http_code}\n" https://api.deepseek.com'
  ```

  Ответы 302 и 401 — это «доступно».
