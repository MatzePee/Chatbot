"""Regression: Subs search field may not retain desktop flex rules on phones."""
from pathlib import Path


def test_subs_search_has_mobile_flex_override():
    template = Path('app/templates/chats.html').read_text()
    assert '<label class="chat-filter-search">Suche' in template
    assert 'style="flex:2">Suche' not in template
    assert '@media (max-width: 900px)' in template
    assert '#chat-filter > .chat-filter-search { flex: 1 1 100%; }' in template
    assert 'id="chat-search" name="q"' in template
    assert 'name="view"' in template and 'name="sort"' in template
