"""Add the isolated image-variant table before character schema validation."""
from sqlalchemy import inspect


def _upgrade(connection):
    tables = set(inspect(connection).get_table_names())
    if {"character", "media_asset"}.issubset(tables):
        from autoposter.models import MediaImageEdit
        MediaImageEdit.__table__.create(connection, checkfirst=True)


async def upgrade(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)
