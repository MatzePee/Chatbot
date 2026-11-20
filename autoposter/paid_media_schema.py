"""Additive Telegram paid-media fields for existing character databases."""
from sqlalchemy import inspect

COLUMNS = {
    'channel': {
        'telegram_mode': "VARCHAR(10) NOT NULL DEFAULT 'free'",
        'telegram_content': "VARCHAR(10) NOT NULL DEFAULT 'mixed'",
        'telegram_interval_days': 'INTEGER NOT NULL DEFAULT 1',
        'telegram_start_date': 'DATE',
    },
    'media_asset': {'telegram_price_stars': 'INTEGER'},
    'media_set': {'telegram_price_stars': 'INTEGER'},
    'post': {'price_stars': 'INTEGER'},
}


def _upgrade(connection):
    schema = inspect(connection)
    tables = set(schema.get_table_names())
    for table, additions in COLUMNS.items():
        if table not in tables:
            continue
        present = {column['name'] for column in schema.get_columns(table)}
        for name, definition in additions.items():
            if name not in present:
                connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


async def upgrade(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)


def upgrade_sqlite(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, additions in COLUMNS.items():
        if table not in tables:
            continue
        present = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for name, definition in additions.items():
            if name not in present:
                connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
    connection.commit()
