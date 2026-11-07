"""Queue navigation stays GET-only and opens the intended fan settings."""
from html.parser import HTMLParser
from fastapi.testclient import TestClient
from app import db,main


class Nav(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.settings=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='a':self.links.append(a)
        if tag=='details' and a.get('id','').startswith('settings-'):self.settings.append(a)


def parse(html):
    p=Nav();p.feed(html);return p


def test_queue_has_reload_links_above_and_below_drafts(monkeypatch):
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:False)
    db.create_draft(user_uuid='fan-one',status='pending',generated_text='safe')
    page=TestClient(main.app).get('/queue').text
    assert page.count('Seite neu laden')==2
    first=page.index('Seite neu laden');card=page.index('id="draft-');last=page.rindex('Seite neu laden')
    assert first<card<last
    nav=parse(page)
    assert len([a for a in nav.links if a.get('href')=='/queue' and a.get('class')=='btn queue-reload'])==2


def test_queue_name_opens_exact_fan_settings(monkeypatch):
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:False)
    db.upsert_chat('fan-a','shared','First fan')
    db.upsert_chat('fan-b','shared','Second fan')
    db.create_draft(user_uuid='fan-a',display_name='First fan',status='pending',generated_text='safe')
    client=TestClient(main.app)
    queue=client.get('/queue').text
    assert 'href="/chats?view=all&amp;q=fan-a#settings-fan-a"' in queue
    response=client.get('/chats?view=all&q=fan-a')
    assert response.status_code==200
    assert 'First fan' in response.text and 'Second fan' not in response.text
    settings=[a for a in parse(response.text).settings if a.get('id')=='settings-fan-a']
    assert len(settings)==1 and 'open' in settings[0]


def test_queue_reload_exists_when_empty(monkeypatch):
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:False)
    page=TestClient(main.app).get('/queue').text
    assert page.count('Seite neu laden')==2
    assert 'Keine wartenden Freigaben' in page
