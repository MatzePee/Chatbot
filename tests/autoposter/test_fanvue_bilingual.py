import json
from unittest.mock import AsyncMock

import pytest

from autoposter.models import Persona, Post
from autoposter.schemas import GenerateRequest
from autoposter.api.posts import generate
from autoposter.services import llm as service
from autoposter.services import media
from tests.autoposter.test_duplicates import make_channel, make_image


EN = "🇺🇸 Blue knit and a cheeky look. 😏"
DE = "🇩🇪 Blauer Strick und ein frecher Blick. 😏"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,translation", [
    (EN + "\n\n" + DE, DE),
    (EN + " " + DE, DE),
    (EN, DE),
    (EN, ""),
])
async def test_both_languages_persist_in_post_body(db, monkeypatch, text, translation):
    persona = Persona(name="Bilingual", slug="bilingual", language="en",
                      media_system_prompt="Exactly two paragraphs: 🇺🇸 English first, then 🇩🇪 German. No hashtags.")
    db.add(persona)
    await db.flush()
    channel = await make_channel(db, "Fanvue bilingual", "fanvue")
    channel.persona_id = persona.id
    asset = await media.ingest(db, filename="knit.jpg", data=make_image())
    asset.ai_description = "Blue knit and a mirror wall."
    post = Post(channel_id=channel.id, type="image_single", media_asset_ids=[str(asset.id)],
                body_text="", status="needs_review")
    db.add(post)
    await db.flush()
    call = AsyncMock(return_value={"choices": [{"message": {"content": json.dumps({"variants": [{
        "text": text, "translation_de": translation, "hashtags": [],
    }]})}, "finish_reason": "stop"}]})
    translate = AsyncMock(return_value=DE)
    monkeypatch.setattr(service.llm, "_call", call)
    monkeypatch.setattr(service.llm, "translate_german", translate)
    result = await generate(GenerateRequest(post_id=post.id, variants=1), db=db, _=None)
    assert result.variants[0]["text"] == EN + "\n\n" + DE
    await db.flush()
    await db.refresh(post)
    assert post.body_text == EN + "\n\n" + DE
    assert translate.await_count == (1 if not translation else 0)
    prompt = call.call_args.kwargs["messages"][1]["content"]
    assert "ALLEN" in prompt and "ersetzt NIEMALS" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("text,finish", [
    (DE, "stop"), (EN, "length"), (EN + "\n\n🇩🇪   ", "stop"),
])
async def test_incomplete_caption_is_rejected(db, monkeypatch, text, finish):
    persona = Persona(name="Bilingual", slug="bilingual", language="en",
                      media_system_prompt="🇺🇸 English\n\n🇩🇪 German")
    db.add(persona)
    await db.flush()
    monkeypatch.setattr(service.llm, "_call", AsyncMock(return_value={"choices": [{
        "message": {"content": json.dumps({"variants": [{"text": text}]})},
        "finish_reason": finish,
    }]}))
    with pytest.raises(RuntimeError, match="Sprachversionen|abgeschnitten"):
        await service.llm.generate_post_text(db, persona=persona, platform="fanvue",
            channel_id=None, image_descriptions=["Blue knit"], variants=1, max_chars=5000)
