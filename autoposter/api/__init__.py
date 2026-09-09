"""API-Router-Zusammenbau."""
from fastapi import APIRouter

from autoposter.api import system, assignments, auth, channels, dashboard, media, posts

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(channels.router)
api_router.include_router(media.router)
api_router.include_router(media.sets_router)
api_router.include_router(media.views_router)
api_router.include_router(media.maintenance_router)
api_router.include_router(assignments.router)
api_router.include_router(posts.router)
api_router.include_router(posts.calendar_router)
api_router.include_router(posts.runs_router)
api_router.include_router(dashboard.router)

