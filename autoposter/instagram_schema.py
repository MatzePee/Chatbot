"""Add Instagram disclosure preference before existing-schema validation."""
from sqlalchemy import inspect

def _upgrade(connection):
    schema = inspect(connection)
    if 'channel' in schema.get_table_names() and 'instagram_is_ai_generated' not in {c['name'] for c in schema.get_columns('channel')}:
        connection.exec_driver_sql('ALTER TABLE "channel" ADD COLUMN "instagram_is_ai_generated" BOOLEAN NOT NULL DEFAULT FALSE')

async def upgrade(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)
