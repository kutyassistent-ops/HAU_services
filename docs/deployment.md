# Запуск и развёртывание

## Требования

- Python 3.12;
- pip;
- SQLite 3.35+ (идёт в составе Python);
- свободный порт 5000 для локального запуска или 8000 для Docker;
- для production — gunicorn/waitress и, желательно, reverse proxy.

## Локальный запуск

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Создать демонстрационную базу (удаляет текущую instance/utility_billing.db)
python seed_demo.py

python app.py
```

Приложение будет доступно по адресу <http://127.0.0.1:5000>.

Если база уже существует, `seed_demo.py` запускать не нужно — при старте
приложение создаст отсутствующие таблицы и применит миграции. Существующие
данные при этом сохраняются.

Для разработки установите также `requirements-dev.txt` — там pytest,
pytest-cov и ruff.

## Доступ по локальной сети

По умолчанию `FLASK_HOST=0.0.0.0`, поэтому сервер слушает все интерфейсы.

1. Узнайте IPv4-адрес:

   ```powershell
   ipconfig
   ```

2. Запустите `python app.py`.
3. На другом устройстве откройте `http://<IPv4>:5000`, например
   `http://192.168.1.10:5000`.

Если соединение не устанавливается:

- разрешите Python входящие подключения в брандмауэре Windows для сети
  «Частная»;
- создайте правило от имени администратора:

  ```powershell
  New-NetFirewallRule -DisplayName "ЖКУ-Учёт (5000)" -Direction Inbound `
    -Protocol TCP -LocalPort 5000 -Action Allow
  ```

- проверьте, что устройства находятся в одной подсети и точка доступа не
  включает изоляцию клиентов;
- убедитесь, что порт 5000 не занят другим процессом.

Чтобы вернуть доступ только с локального компьютера:

```powershell
$env:FLASK_HOST = "127.0.0.1"
python app.py
```

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `SECRET_KEY` | учебный ключ | подпись сессий и CSRF; в сети обязательно задайте свой |
| `DATABASE_PATH` | `instance/utility_billing.db` | путь к файлу SQLite |
| `FLASK_HOST` | `0.0.0.0` | адрес прослушивания встроенного сервера |
| `FLASK_PORT` | `5000` | порт встроенного сервера |
| `FLASK_DEBUG` | `0` | `1` включает debug и автоперезагрузку (только разработка) |
| `LOG_LEVEL` | `INFO` | уровень логирования |
| `LOG_FILE` | `instance/app.log` | файл логов |
| `SESSION_COOKIE_SECURE` | `0` | `1` — cookie только по HTTPS |
| `SESSION_DAYS` | `7` | срок жизни сессии, дней |
| `REMEMBER_DAYS` | `14` | срок «запомнить меня», дней |
| `MAX_LOGIN_ATTEMPTS` | `5` | неудачных попыток до блокировки |
| `LOGIN_LOCKOUT_SECONDS` | `300` | длительность блокировки, секунд |
| `NOTIFICATIONS_ENABLED` | `0` | `1` включает email-уведомления |
| `SMTP_HOST` | пусто | SMTP-сервер |
| `SMTP_PORT` | `587` | SMTP-порт |
| `SMTP_USER` | пусто | логин SMTP |
| `SMTP_PASSWORD` | пусто | пароль SMTP |
| `SMTP_FROM` | `no-reply@example.ru` | адрес отправителя |
| `SMTP_USE_TLS` | `1` | использовать STARTTLS |
| `SMTP_TIMEOUT` | `10` | таймаут SMTP, секунд |

Пример для PowerShell:

```powershell
$env:SECRET_KEY = "длинная-случайная-строка"
$env:SESSION_COOKIE_SECURE = "0"
$env:FLASK_DEBUG = "0"
python app.py
```

Файл `.env` автоматически не читается — переменные нужно задать в оболочке,
в сервисе или в конфигурации контейнера. Пример всех переменных есть в
[`.env.example`](../.env.example).

## Docker

В репозитории есть `Dockerfile` и `docker-compose.yml`.

```bash
docker compose up --build
```

Что настроено:

- приложение запускается gunicorn на порту `8000`;
- база и логи лежат в томе `hau_data` (`/data`);
- контейнер работает от непривилегированного пользователя;
- healthcheck обращается к `/healthz`;
- SMTP и `SECRET_KEY` передаются через переменные окружения.

Перед публикацией задайте ключ:

```bash
SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
docker compose up -d --build
```

Остановить и удалить контейнер без удаления данных:

```bash
docker compose down
```

Удалить вместе с томом (все данные пропадут):

```bash
docker compose down -v
```

## Production

Встроенный сервер Flask не предназначен для production. Используйте
WSGI-сервер и, желательно, reverse proxy.

### Gunicorn (Linux, Docker)

```bash
pip install -r requirements-prod.txt
gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 60 wsgi:app
```

`wsgi.py` создаёт приложение через `create_app()`.

### Waitress (Windows)

```powershell
pip install waitress
waitress-serve --listen=127.0.0.1:8000 --call app:create_app
```

### Nginx

Минимальный пример:

```nginx
server {
    listen 80;
    server_name example.ru;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Для HTTPS добавьте сертификат (например, Let's Encrypt) и перенаправление с
HTTP. При работе за прокси задайте `ProxyFix`, если приложение должно
доверять заголовкам `X-Forwarded-*`. При HTTPS включите
`SESSION_COOKIE_SECURE=1`.

### Чек-лист перед публикацией

- [ ] задан собственный `SECRET_KEY`;
- [ ] `FLASK_DEBUG=0`;
- [ ] включён HTTPS и `SESSION_COOKIE_SECURE=1`;
- [ ] приложение слушает `127.0.0.1`, если перед ним стоит reverse proxy;
- [ ] каталог `instance/` или том `/data` на постоянном диске и включён в
      резервное копирование;
- [ ] ограничен доступ к `instance/app.log`;
- [ ] проверены права ролей и тестовые учётные записи;
- [ ] настроены бэкапы SQLite (`scripts/backup_db.py`);
- [ ] при необходимости настроены SMTP-переменные и `NOTIFICATIONS_ENABLED`;
- [ ] проверен лимит попыток входа; при нескольких процессах дополнительно
      ограничьте вход на уровне прокси.

### SQLite в production

SQLite подходит для одного узла и небольшой нагрузки. Учитывайте:

- не запускайте несколько процессов приложения с записью в один файл без
  необходимости; при нескольких gunicorn-воркерах лимит входов в памяти
  процесса будет отдельным для каждого воркера;
- делайте резервные копии через `scripts/backup_db.py` или `.backup`, а не
  простым копированием во время записи;
- для роста нагрузки переходите на PostgreSQL.

```powershell
python scripts/backup_db.py --keep 14
```

## Обновление приложения

1. Сделайте резервную копию базы.
2. Обновите код и зависимости.
3. Перезапустите приложение — `schema.sql` создаст отсутствующие объекты, а
   `migrations.py` применит новые миграции и запишет их в
   `schema_migrations`.
4. Проверьте `/healthz` и журнал приложения.

Миграции идемпотентны: повторный запуск не меняет уже обновлённую схему.
Старые данные не удаляются. Если миграция не требуется, версия просто
отсутствует в `schema_migrations`.

## PWA и офлайн-режим

Приложение можно установить на телефон или компьютер как PWA:

1. откройте сайт в браузере (лучше по HTTPS);
2. выберите «Установить приложение» или «Добавить на главный экран»;
3. service worker `/sw.js` закеширует статические файлы и покажет
   офлайн-заглушку при потере сети.

Динамические страницы и данные в офлайн-кеш не попадают.

## CI

В репозитории есть workflow `.github/workflows/tests.yml`:

- job `test` устанавливает `requirements-dev.txt`, запускает `ruff check .`
  и `pytest` с покрытием (порог 85 %);
- job `docker` собирает образ `hau-services:ci`.

Workflow не требует секретов и использует временную БД из тестовых фикстур.

## Публикация на GitHub

```powershell
git init
git add .
git commit -m "ЖКУ-Учёт: обновление функциональности"
git branch -M main
git remote add origin https://github.com/<пользователь>/<репозиторий>.git
git push -u origin main
```

`.gitignore` исключает виртуальное окружение, кэши, покрытие, логи,
резервные копии и локальную базу `instance/utility_billing.db`. После
клонирования база создаётся командой `python seed_demo.py` или автоматически
при первом запуске (пустая схема).

## Диагностика

| Проблема | Решение |
|---|---|
| `Address already in use` | закройте процесс на порту 5000/8000 или задайте другой порт |
| страница не открывается с телефона | проверьте IP, firewall и одну подсеть |
| `database is locked` | закройте лишние процессы, проверьте WAL и бэкапы |
| после обновления нет новых полей | проверьте `schema_migrations` и лог миграций |
| не работает вход после рестарта | задайте стабильный `SECRET_KEY`, иначе сессии сбросятся |
| ошибка тарифа при вводе показаний | создайте тариф с датой не позже даты показания |
| письма не отправляются | проверьте `NOTIFICATIONS_ENABLED`, SMTP-переменные и лог |
| слишком много неудачных попыток входа | подождите `LOGIN_LOCKOUT_SECONDS` или перезапустите процесс |
