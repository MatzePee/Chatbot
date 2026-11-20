"""Workspace control plane. A Character is not a Persona or a channel."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from autoposter.db import get_db
from autoposter.deps import current_user, require_admin
from autoposter.models import Character, AppSetting, User
from autoposter.workspaces import character_context

router=APIRouter(prefix='/characters',tags=['characters'])


@router.get('/mode')
async def mode():
    import os
    return {'enabled':os.environ.get('MP_CHARACTERS_ENABLED') == '1'}


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1,max_length=120)

    profile_image: str | None = Field(default=None, max_length=750000)

    @field_validator('profile_image')
    @classmethod
    def valid_image(cls, value):
        if value is None:
            return value
        import base64
        import binascii
        prefixes = {'data:image/jpeg;base64,': b'\xff\xd8\xff',
                    'data:image/png;base64,': b'\x89PNG\r\n\x1a\n',
                    'data:image/webp;base64,': b'RIFF'}
        for prefix, signature in prefixes.items():
            if value.startswith(prefix):
                try:
                    data = base64.b64decode(value[len(prefix):], validate=True)
                except (ValueError, binascii.Error):
                    break
                if data.startswith(signature) and (signature != b'RIFF' or data[8:12] == b'WEBP'):
                    return value
        raise ValueError('Bitte ein gültiges JPG-, PNG- oder WebP-Bild auswählen')

    @field_validator('name')
    @classmethod
    def meaningful(cls,value):
        value=value.strip()
        if not value:
            raise ValueError('Name darf nicht leer sein')
        return value


class CharacterOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: UUID
    name: str
    profile_image: str | None = None
    planning_enabled: bool
    chat_enabled: bool
    is_archived: bool


@router.get('',response_model=list[CharacterOut])
async def list_characters(db: AsyncSession=Depends(get_db), _:User=Depends(current_user)):
    characters = (await db.scalars(select(Character).where(Character.is_archived.is_(False))
                                  .order_by(Character.created_at,Character.id))).all()
    keys = [f'{char.id}:profile' for char in characters]
    profiles = (await db.scalars(select(AppSetting).where(AppSetting.key.in_(keys)))).all() if keys else []
    images = {row.key: row.value.get('image') for row in profiles}
    return [CharacterOut.model_validate(char).model_copy(update={
        'profile_image': images.get(f'{char.id}:profile')}) for char in characters]


@router.post('',status_code=201,response_model=CharacterOut)
async def create_character(payload:CharacterCreate,db:AsyncSession=Depends(get_db),
                           _:User=Depends(require_admin)):
    char=Character(name=payload.name,planning_enabled=False,chat_enabled=False,is_archived=False)
    db.add(char);await db.flush()
    # This control-plane transaction has not loaded any character AppSetting.
    # Flush the namespaced safe default atomically with the workspace creation.
    with character_context(char.id):
        row=AppSetting(key='runtime',value={'global_pause':True,'dry_run':True})
        db.add(row)
        await db.flush()
    if payload.profile_image is not None:
        db.add(AppSetting(key=f'{char.id}:profile', value={'image': payload.profile_image}))
        await db.flush()
    return CharacterOut.model_validate(char).model_copy(update={'profile_image': payload.profile_image})


class CharacterRename(CharacterCreate):
    model_config = ConfigDict(extra='forbid')


@router.patch('/{character_id}', response_model=CharacterOut)
async def rename_character(character_id: UUID, payload: CharacterRename,
                           db: AsyncSession = Depends(get_db),
                           _: User = Depends(require_admin)):
    char = await db.scalar(select(Character).where(Character.id == character_id).with_for_update())
    if char is None or char.is_archived:
        raise HTTPException(404, 'Character nicht gefunden')
    char.name = payload.name
    await db.flush()
    key = f'{char.id}:profile'
    profile = await db.get(AppSetting, key)
    if 'profile_image' in payload.model_fields_set:
        if profile is None:
            profile = AppSetting(key=key, value={'image': payload.profile_image})
            db.add(profile)
        else:
            profile.value = {'image': payload.profile_image}
        await db.flush()
    return CharacterOut.model_validate(char).model_copy(update={
        'profile_image': profile.value.get('image') if profile else None})


