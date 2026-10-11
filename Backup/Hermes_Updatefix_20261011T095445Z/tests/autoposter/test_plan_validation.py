from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from autoposter.api import posts
from autoposter.schemas import PlanRangeRequest, PlanRangeResult


@pytest.mark.parametrize("image,text,sub,free", [
    (0, 0, 2, 1), (0, 0, 1, 0), (0, 0, 0, 1),
    (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 0),
])
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.asyncio
async def test_plan_endpoints_accept_each_post_type(monkeypatch, image, text, sub, free, background):
    request = PlanRangeRequest(
        date_from=date(2026, 9, 18), date_to=date(2026, 9, 18),
        image_posts_per_day=image, text_posts_per_day=text,
        sub_posts_per_day=sub, free_posts_per_day=free,
        dry_run=not background,
    )
    planned = AsyncMock(return_value=PlanRangeResult())
    monkeypatch.setattr(posts.planner, "plan_range", planned)
    launched = []
    monkeypatch.setattr(posts.runs, "create", lambda label: SimpleNamespace(id="test-run"))
    monkeypatch.setattr(posts.runs, "launch", lambda run, work: launched.append(run.id))
    user = SimpleNamespace(id=None)

    async def call():
        if background:
            return await posts.plan_range_start(request, user=user)
        return await posts.plan_range(request, db=None, user=user)

    if not any((image, text, sub, free)):
        with pytest.raises(HTTPException) as error:
            await call()
        assert error.value.status_code == 400
        assert "Mindestens ein Post" in error.value.detail
        assert not launched
        planned.assert_not_awaited()
    elif background:
        assert (await call())["run_id"] == "test-run"
        assert launched == ["test-run"]
    else:
        assert await call() == PlanRangeResult()
        planned.assert_awaited_once_with(None, request, actor_id=None)
