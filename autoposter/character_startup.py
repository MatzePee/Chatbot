"""Read-only activation validation. Startup is not a schema migration."""
from sqlalchemy import inspect, select, text
from autoposter.db import Base, SessionLocal
from autoposter.models import Character


async def validate_installation(engine):
    def validate(connection):
        schema = inspect(connection)
        if '_character_migration' not in schema.get_table_names():
            raise RuntimeError('Explicit character copy migration required before activation')
        marker = connection.execute(text('SELECT version FROM _character_migration')).fetchall()
        if marker != [(1,)]:
            raise RuntimeError('Unsupported character migration receipt')
        for table in Base.metadata.sorted_tables:
            actual = {column['name'] for column in schema.get_columns(table.name)}
            if not set(table.columns.keys()).issubset(actual):
                raise RuntimeError('Character schema incomplete: ' + table.name)
            if 'character_id' in table.c:
                # No owner repair or default assignment during startup.
                missing = connection.execute(text(
                    f'SELECT count(*) FROM "{table.name}" AS owned '
                    'LEFT JOIN character AS root ON owned.character_id=root.id '
                    'WHERE owned.character_id IS NULL OR root.id IS NULL')).scalar_one()
                if missing:
                    raise RuntimeError('Unowned character content: ' + table.name)
    async with engine.connect() as connection:
        await connection.run_sync(validate)
    async with SessionLocal(info={'control_plane': True}) as session:
        return list((await session.scalars(select(Character).where(Character.is_archived.is_(False)))).all())
