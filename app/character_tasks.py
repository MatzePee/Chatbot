"""Explicit owner-carrying threads; never rely on thread-local active selection."""
import os
import threading
from autoposter.workspaces import current_character, character_context, CharacterBoundaryError


class CharacterThread(threading.Thread):
    def __init__(self,*args,**kwargs):
        self._character_id=current_character()
        if self._character_id is None and os.environ.get('MP_CHARACTERS_ENABLED') == '1':
            raise CharacterBoundaryError('Character ID required for worker thread')
        super().__init__(*args,**kwargs)

    @property
    def character_id(self):return self._character_id

    def run(self):
        if self.character_id is not None:
            with character_context(self.character_id):
                return super().run()
        return super().run()
