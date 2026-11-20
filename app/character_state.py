"""Process caches partitioned by immutable URL/worker workspace context."""
from collections.abc import MutableMapping
import copy
import threading
from autoposter.workspaces import current_character, CharacterBoundaryError


def state_owner():
    import os
    owner = current_character()
    if owner is None and os.environ.get('MP_CHARACTERS_ENABLED') == '1':
        raise CharacterBoundaryError('Character context required for worker state')
    return owner


class CharacterLocal:
    """Per-owner state factory (locks/events must never be deep-copied)."""
    def __init__(self, factory):
        self.factory = factory
        self.partitions = {}
        self.lock = threading.RLock()

    def get(self):
        owner = state_owner()
        with self.lock:
            if owner not in self.partitions:
                self.partitions[owner] = self.factory()
            return self.partitions[owner]


class CharacterDict(MutableMapping):
    def __init__(self, initial=None):
        self._initial=copy.deepcopy(initial or {})
        self._partitions={}
        self._lock=threading.RLock()

    def _mapping(self):
        owner=state_owner()
        with self._lock:
            return self._partitions.setdefault(owner,copy.deepcopy(self._initial))

    def __getitem__(self,key):return self._mapping()[key]
    def __setitem__(self,key,value):self._mapping()[key]=value
    def __delitem__(self,key):del self._mapping()[key]
    def __iter__(self):return iter(self._mapping())
    def __len__(self):return len(self._mapping())
    def copy(self):return dict(self._mapping())
    def clear(self):self._mapping().clear()
