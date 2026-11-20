"""Explicit immutable character ownership for request and worker sessions.

No process-wide active character. A session is bound once, before its first query.
Unscoped sessions exist only for legacy mode / explicit control-plane operations.
"""
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import event, inspect, select, literal, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, with_loader_criteria

from autoposter.models import Character, CharacterOwned, AppSetting


class CharacterBoundaryError(ValueError):
    pass


_context: ContextVar[UUID | None] = ContextVar('character_id', default=None)
_control: ContextVar[bool] = ContextVar('character_control_plane', default=False)


@contextmanager
def control_context():
    token = _control.set(True)
    try:
        yield
    finally:
        _control.reset(token)


def current_character() -> UUID | None:
    return _context.get()


def is_control_context() -> bool:
    return _control.get()


@contextmanager
def character_context(character_id):
    value = UUID(str(character_id))
    token = _context.set(value)
    try:
        yield value
    finally:
        _context.reset(token)


class WorkspaceAsyncSession(AsyncSession):
    """Capture ownership at construction, including jobs and upload transactions."""
    def __init__(self, *args, **kwargs):
        import os
        info = dict(kwargs.pop('info', {}) or {})
        owner = current_character()
        if owner is not None:
            info['character_id'] = owner
        elif os.environ.get('MP_CHARACTERS_ENABLED') == '1' and not (info.get('control_plane') or _control.get()):
            raise CharacterBoundaryError('Character context required for database access')
        super().__init__(*args, info=info, **kwargs)

    async def get(self, *args, **kwargs):
        owner=self.info.get('character_id')
        if owner is not None and owner != current_character():
            raise CharacterBoundaryError('Character context changed while session was open')
        return await super().get(*args, **kwargs)


@asynccontextmanager
async def character_session(bind, character_id):
    owner = UUID(str(character_id))
    with character_context(owner):
        async with WorkspaceAsyncSession(bind, expire_on_commit=False) as session:
            character = await session.get(Character, owner)
            if character is None or character.is_archived:
                raise CharacterBoundaryError('Character workspace not found')
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise


@event.listens_for(Session, 'do_orm_execute')
def _scope_statement(state):
    owner = state.session.info.get('character_id')
    if owner is None:
        return
    if owner != current_character():
        raise CharacterBoundaryError('Character context changed while session was open')
    if not state.is_orm_statement:
        raise CharacterBoundaryError('Unscoped SQL is forbidden in a character session')
    if state.is_select:
        state.statement = state.statement.options(with_loader_criteria(
            CharacterOwned, lambda model: model.character_id == owner,
            include_aliases=True))
        state.statement = state.statement.options(with_loader_criteria(
            AppSetting, AppSetting.key.like(literal(f'{owner}:%', String())),
            include_aliases=True))
    elif state.is_update or state.is_delete:
        mapper = state.bind_mapper
        if mapper is None or not issubclass(mapper.class_, CharacterOwned):
            raise CharacterBoundaryError('Bulk write target is not character-owned')
        if state.is_update:
            # Ordered values and executemany dictionaries have different SQLAlchemy
            # representations. Reject them rather than accidentally missing a key.
            if state.statement._ordered_values or isinstance(state.parameters, (list, tuple)):
                raise CharacterBoundaryError('Use validated ORM writes instead of ordered/batched bulk updates')
            changed = {getattr(key, 'key', str(key)) for key in state.statement._values or {}}
            if isinstance(state.parameters, dict):
                changed.update(state.parameters)
            # Reassignments must go through flush validation, never bulk DML.
            if any(key == 'character_id' or key.endswith('_id') or key.endswith('_ids')
                   for key in changed):
                raise CharacterBoundaryError('Bulk ownership/reference changes are forbidden')
        state.statement = state.statement.where(mapper.class_.character_id == owner)
    else:
        raise CharacterBoundaryError('Use ORM add/flush for validated character inserts')


@event.listens_for(Session, 'before_commit')
def _owner_before_commit(session):
    owner = session.info.get('character_id')
    if owner is not None and owner != current_character():
        raise CharacterBoundaryError('Character context changed while session was open')


@event.listens_for(Session, 'before_flush')
def _own_rows(session, _flush_context, _instances):
    owner = session.info.get('character_id')
    if owner is None:
        return
    if owner != current_character():
        # Flush can emit DML without do_orm_execute (including AppSetting).
        raise CharacterBoundaryError('Character context changed while session was open')
    for obj in session.new | session.dirty | session.deleted:
        if not isinstance(obj, CharacterOwned):
            continue
        existing = obj.character_id
        if existing is None and obj in session.new:
            obj.character_id = owner
        elif existing != owner:
            raise CharacterBoundaryError('Cross-character write rejected')
        history = inspect(obj).attrs.character_id.history
        if history.deleted and history.deleted[0] != owner:
            raise CharacterBoundaryError('Character ownership is immutable')
    for obj in session.new | session.dirty:
        if isinstance(obj, CharacterOwned):
            _validate_references(session, obj, owner)


def _assert_reference(session, table, key, value, owner):
    if value is None:
        return
    try:
        value = UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CharacterBoundaryError('Invalid referenced ID') from exc
    for candidate in session.new:
        if isinstance(candidate, CharacterOwned) and candidate.__table__ is table:
            if getattr(candidate, key) == value and candidate.character_id == owner:
                return
    actual = session.connection().execute(select(table.c.character_id).where(
        table.c[key] == value)).scalar_one_or_none()
    if actual != owner:
        raise CharacterBoundaryError('Referenced object not found in this character')


def _validate_references(session, obj, owner):
    from autoposter import models
    mapper = inspect(type(obj))
    for column in mapper.columns:
        for fk in column.foreign_keys:
            target = fk.column
            if 'character_id' in target.table.c:
                _assert_reference(session, target.table, target.key,
                                  getattr(obj, column.key), owner)
    # These legacy UUID/JSON references are intentionally not SQL foreign keys.
    extra = {
        'avatar_media_id': models.MediaAsset, 'cover_media_id': models.MediaAsset,
        'preview_media_id': models.MediaAsset, 'media_preview_id': models.MediaAsset,
        'media_set_id': models.MediaSet, 'persona_id': models.Persona,
        'post_id': models.Post,
        'media_asset_ids': models.MediaAsset, 'assigned_channel_ids': models.Channel,
        'used_channel_ids': models.Channel, 'scheduled_channel_ids': models.Channel,
    }
    for field, model in extra.items():
        if not hasattr(obj, field):
            continue
        values = getattr(obj, field)
        if values is None:
            continue
        for value in values if isinstance(values, list) else [values]:
            _assert_reference(session, model.__table__, 'id', value, owner)
    for relation in mapper.relationships:
        loaded = inspect(obj).attrs[relation.key].loaded_value
        from sqlalchemy.orm.attributes import NO_VALUE
        if loaded is NO_VALUE:
            continue
        for related in loaded if relation.uselist else [loaded]:
            if isinstance(related, CharacterOwned) and related.character_id != owner:
                raise CharacterBoundaryError('Cross-character relationship rejected')
