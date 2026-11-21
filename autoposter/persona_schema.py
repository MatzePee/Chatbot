"""Add per-persona hashtag generation preference before schema validation."""
from sqlalchemy import inspect

def _upgrade(connection):
    schema = inspect(connection)
    if 'persona' in schema.get_table_names() and 'generate_separate_hashtags' not in {c['name'] for c in schema.get_columns('persona')}:
        connection.exec_driver_sql('ALTER TABLE "persona" ADD COLUMN "generate_separate_hashtags" BOOLEAN NOT NULL DEFAULT TRUE')

async def upgrade(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)
