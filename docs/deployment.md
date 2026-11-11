# Запуск и развёртывание

## Требования

- Python 3.12;
- pip;
- SQLite 3.35+ (идёт в составе Python);
- свободный порт 5000 для локального запуска.

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
приложение само применит `schema.sql`.

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
| `FLASK_HOST` | `0.0.0.0` | адрес прослушивания |
| `FLASK_PORT` | `5000` | порт |
| `FLASK_DEBUG` | `0` | `1` включает debug и автоперезагрузку |
| `LOG_LEVEL` | `INFO` | уровень логирования |
| `LOG_FILE` | `instance/app.log` | файл логов |

Пример для PowerShell:

```powershell
$env:SECRET_KEY = "длинная-случайная-строка"
$env:FLASK_HOST = "0.0.0.0"
$env:FLASK_PORT = "5000"
$env:FLASK_DEBUG = "0"
python app.py
```

Файл `.env` автоматически не читается — переменные нужно задать в оболочке,
в сервисе или в конфигурации контейнера.

## Production

Встроенный сервер Flask не предназначен для production. Используйте
WSGI-сервер и, желательно, reverse proxy.

### Waitress (Windows)

```powershell
pip install waitress
waitress-serve --listen=127.0.0.1:8000 --call app:create_app
```

### Gunicorn (Linux)

```bash
pip install gunicorn
gunicorn --workers 3 --bind 127.0.0.1:8000 'app:create_app()'
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
HTTP. Flask-Login использует cookie; при работе за прокси задайте
`ProxyFix`, если приложение должно доверять заголовкам `X-Forwarded-*`.

### Чек-лист перед публикацией

- [ ] задан собственный `SECRET_KEY`;
- [ ] `FLASK_DEBUG=0`;
- [ ] приложение слушает `127.0.0.1`, если перед ним стоит reverse proxy;
- [ ] настроен HTTPS;
- [ ] каталог `instance/` на постоянном диске и включён в резервное
      копирование;
- [ ] ограничен доступ к `instance/app.log`;
- [ ] проверены права ролей и тестовые учётные записи;
- [ ] настроены бэкапы SQLite;
- [ ] при необходимости ограничены попытки входа на уровне прокси.

### SQLite в production

SQLite подходит для одного узла и небольшой нагрузки. Учитывайте:

- не запускайте несколько процессов приложения с записью в один файл без
  необходимости;
- делайте резервные копии через `.backup`, а не простым копированием
  во время записи;
- для роста нагрузки переходите на PostgreSQL.

```powershell
sqlite3 instance/utility_billing.db ".backup instance/backup.db"
```

## Обновление приложения

1. Остановите сервер.
2. Сделайте резервную копию `instance/utility_billing.db`.
3. Обновите код и зависимости.
4. Запустите приложение — `schema.sql` применится автоматически.
5. Для изменения существующих таблиц используйте отдельные миграции:
   `CREATE TABLE IF NOT EXISTS` не добавляет новые колонки в старые таблицы.

## CI

В репозитории есть workflow `.github/workflows/tests.yml`: при push и pull
request запускаются 39 тестов с покрытием на Python 3.12. Workflow не
требует секретов и использует временную БД из тестовых фикстур.

## Публикация на GitHub

```powershell
git init
git add .
git commit -m "Initial commit: ЖКУ-Учёт"
git branch -M main
git remote add origin https://github.com/<пользователь>/<репозиторий>.git
git push -u origin main
```

`.gitignore` уже исключает виртуальное окружение, кэши, покрытие, логи и
локальную базу `instance/utility_billing.db`. После клонирования база
создаётся командой `python seed_demo.py` или автоматически при первом
запуске (пустая схема).

## Диагностика

| Проблема | Решение |
|---|---|
| `Address already in use` | закройте процесс на порту 5000 или задайте `FLASK_PORT` |
| страница не открывается с телефона | проверьте IP, firewall и одну подсеть |
| `database is locked` | закройте лишние процессы, проверьте WAL и бэкапы |
| после обновления нет новых полей | добавьте миграцию или пересоздайте БД |
| не работает вход после рестарта | задайте стабильный `SECRET_KEY`, иначе сессии сбросятся |
| ошибка тарифа при вводе показаний | создайте тариф с датой не позже даты показания |
