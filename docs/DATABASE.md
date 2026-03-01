# База данных

## Поддерживаемые режимы

- MySQL (`MYSQL_URL`)
- SQLite (`DB_PATH`)

Оба режима используются через SQLAlchemy.

## Выбор режима

- Для MySQL задайте `MYSQL_URL`.
- Для SQLite задайте `DB_PATH` и не задавайте `MYSQL_URL`.
- Если `MYSQL_URL` не задан, приложение по умолчанию использует SQLite файл в `BOT_DATA_DIR/db/bot_database.db`.

## Что хранится

- Пользователи и роли.
- Базы знаний, чанки, import logs.
- Async jobs (`/jobs/{id}`).
- ASR/analytics сущности.

## Практика эксплуатации

- Используйте отдельную БД для production.
- Делайте регулярные backup.
- Проверяйте применимость миграций перед релизом.

## Backup

### SQLite
```bash
copy bot_database.db bot_database_backup.db
```

### MySQL
```bash
mysqldump -u <user> -p <db_name> > backup.sql
```
