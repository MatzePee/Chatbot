"""Control-plane orchestration; each actual worker is immutable-owner bound."""
import asyncio
import logging
from autoposter import character_dispatch
from autoposter.workspaces import character_context
from app import poller

logger = logging.getLogger(__name__)


class CharacterPollers:
    def __init__(self):
        self.owners = set()
        self.task = None
        self.lock = asyncio.Lock()

    async def reconcile(self):
        async with self.lock:
            wanted = set(await character_dispatch.active_ids())
            for owner in sorted(self.owners - wanted, key=str):
                with character_context(owner):
                    await asyncio.to_thread(poller.stop)
                self.owners.remove(owner)
            for owner in sorted(wanted - self.owners, key=str):
                with character_context(owner):
                    await asyncio.to_thread(poller.start)
                self.owners.add(owner)

    async def _loop(self):
        while True:
            await asyncio.sleep(15)
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Character poller reconciliation failed')

    async def start(self):
        if self.task is not None:
            return
        await self.reconcile()
        self.task = asyncio.create_task(self._loop(), name='character-poller-control')

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        async with self.lock:
            for owner in sorted(self.owners, key=str):
                with character_context(owner):
                    await asyncio.to_thread(poller.stop)
            self.owners.clear()
