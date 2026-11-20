"""Adapter-Registry."""
from __future__ import annotations

from typing import Dict

from autoposter.adapters.base import (  # noqa: F401
    AdapterError,
    ChannelAdapter,
    ChannelLimits,
    Credentials,
    MediaRef,
    PublishResult,
)
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.adapters.telegram import TelegramAdapter
from autoposter.adapters.x import XAdapter
from autoposter.adapters.buffer import BufferAdapter

_REGISTRY: Dict[str, object] = {
    "instagram": BufferAdapter(),
    "telegram": TelegramAdapter(),
    "x": XAdapter(),
    "fanvue": FanvueAdapter(),
}


def get_adapter(platform: str):
    try:
        return _REGISTRY[platform]
    except KeyError:
        raise AdapterError(f"Unbekannte Plattform: {platform}")


def all_platforms() -> list:
    return sorted(_REGISTRY.keys())
