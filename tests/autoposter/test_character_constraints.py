import pytest
from sqlalchemy.exc import IntegrityError
from autoposter import models
from autoposter.workspaces import character_session


@pytest.mark.asyncio
async def test_persona_slug_is_unique_per_character_not_installation(db):
    a,b = models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]); await db.commit()
    for owner in (a.id, b.id):
        async with character_session(db.bind, owner) as session:
            session.add(models.Persona(slug='main', name='Own persona'))
    with pytest.raises(IntegrityError):
        async with character_session(db.bind, a.id) as session:
            session.add(models.Persona(slug='main', name='Duplicate'))
