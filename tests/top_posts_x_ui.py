"""Offline Top Posts X UI regression; no real X requests or reuse actions."""
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    data={'total_posts':4,'measured_posts':3,'missing_views':1,'collection':{'status':'ok','completed_at':'2026-11-01T00:00:00Z'},'items':[]}
    for i,band,state in [(1,'red','ready'),(2,'yellow','available'),(3,'green','scheduled')]:
        data['items'].append(dict(rank=i,post_id=str(i),channel_name='Kanal '+str(i),channel_handle='@account'+str(i),
                                 channel_id=str(i),published_at='2026-10-01T12:00:00Z',age_days=[0,14,20,60][i],age_band=band,
                                 views=10000-i,text='<script>bad()</script> als Text',media=[],external_url='https://x.com/i/status/'+str(i),
                                 can_reuse=state=='ready',reuse_state=state,reuse_label={'ready':'Erneut einplanen','available':'Bereits frei zum Verplanen','scheduled':'Bereits eingeplant'}[state]))
    for item in data['items']:
        item['targets']=[dict(channel_id=item['channel_id'],name=item['channel_name'],platform='x',watermark_enabled=False,
                              can_reuse=item['can_reuse'],reuse_state=item['reuse_state'],reuse_label=item['reuse_label']),
                         dict(channel_id='4',name='Anderer X-Kanal',platform='x',watermark_enabled=True,can_reuse=True,reuse_state='ready',reuse_label='Erneut einplanen'),
                         dict(channel_id='5',name='Geplanter Kanal',platform='x',watermark_enabled=True,can_reuse=False,reuse_state='scheduled',reuse_label='Bereits eingeplant'),
                         dict(channel_id='6',name='Freier Kanal',platform='x',watermark_enabled=False,can_reuse=False,reuse_state='available',reuse_label='Bereits frei zum Verplanen')]
    for item in data['items']:
        for target in item['targets']:
            target['publications']=[]
        item['targets'][0]['publications']=[dict(published_at=item['published_at'],age_days=item['age_days'],age_band=item['age_band'],asset_ids=[],external_url=item['external_url'])]
        item['targets'][1]['publications']=[dict(published_at='2026-09-01T12:00:00Z',age_days=61,age_band='green',asset_ids=[],external_url='https://x.com/i/status/old'),dict(published_at='2026-08-01T12:00:00Z',age_days=92,age_band='green',asset_ids=[],external_url='')]
    for item in data['items']:
        item['targets'] += [dict(channel_id='7',name='Ausgeblendetes Telegram',platform='telegram',can_reuse=True,reuse_label='Einplanen'),
                            dict(channel_id='8',name='Ausgeblendetes Fanvue',platform='fanvue',can_reuse=True,reuse_label='Einplanen')]
    for item in data['items']:
        for target in item['targets']:
            for publication in target.get('publications',[]):
                publication['views']=None
        item['targets'][0]['publications'][0]['views']=0
        item['targets'][1]['publications'][0]['views']=321
        item['targets'][1]['publications'][1]['views']=987
    writes,errors,unexpected=[],[],[]
    fail={'enabled':True}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
        page=browser.new_page(viewport={'width':1280,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route_request(route):
            req=route.request;url=urlparse(req.url);path=url.path
            if url.hostname!='top.test':
                unexpected.append(req.url);return route.abort()
            if path=='/':
                return route.fulfill(content_type='text/html',body='''<!doctype html><meta charset=utf-8><link rel=stylesheet href=/autoposter/app.css><div id=root></div><div id=toasts></div><div id=modals></div><script type=module>import render from '/autoposter/js/pages/top-posts-x.js';document.querySelector('#root').appendChild(await render());</script>''')
            if path.startswith('/autoposter/'):
                file=ROOT/'autoposter/ui'/path.removeprefix('/autoposter/')
                return route.fulfill(body=file.read_bytes(),content_type='text/css' if file.suffix=='.css' else 'text/javascript')
            if path=='/api/v1/analytics/top-posts-x' and req.method=='GET':
                return route.fulfill(json=data)
            if path=='/api/v1/analytics/top-posts-x/1/reuse' and req.method=='POST':
                target_id=parse_qs(url.query)['channel_id'][0]
                writes.append((path,target_id))
                if fail['enabled']:
                    return route.fulfill(status=409,json={'detail':'Testfehler'})
                target=next(t for t in data['items'][0]['targets'] if t['channel_id']==target_id)
                target.update(can_reuse=False,reuse_state='available',reuse_label='Bereits frei zum Verplanen')
                return route.fulfill(json={'ok':True})
            unexpected.append(req.method+' '+path);route.abort()
        page.route('**/*',route_request)
        page.goto('http://top.test')
        expect(page.get_by_role('heading',name='Top Posts X',exact=True)).to_be_visible()
        expect(page.locator('.top-x-post')).to_have_count(3)
        assert page.locator('.top-x-post select').count()==0
        expect(page.locator('.top-x-channel-table')).to_have_count(3)
        first=page.locator('[data-post-id="1"]')
        expect(first.locator('tbody tr')).to_have_count(1)
        expect(first.locator('tbody td')).to_have_count(4)
        assert 'Watermark' not in first.inner_text()
        assert 'Ausgeblendetes Telegram' not in first.inner_text()
        assert 'Ausgeblendetes Fanvue' not in first.inner_text()
        for band in ('red','yellow','green'):
            expect(page.locator('.top-x-channel-box.'+band).first).to_be_visible()
        source=first.locator('[data-channel-id="1"]')
        other=first.locator('[data-channel-id="4"]')
        planned=first.locator('[data-channel-id="5"]')
        available=first.locator('[data-channel-id="6"]')
        expect(planned.locator('button')).to_be_disabled()
        expect(available.locator('button')).to_be_disabled()
        expect(planned).to_contain_text('Noch nie veröffentlicht')
        box_a=source.bounding_box();box_b=other.bounding_box()
        assert abs(box_a['y']-box_b['y'])<2 and box_b['x']>box_a['x'], (box_a,box_b)
        expect(source.locator('.top-x-channel-status')).to_contain_text('verfügbar')
        expect(other.locator('time')).to_have_count(2)
        expect(source.locator('.top-x-publication-views').first).to_have_text('0 Views')
        expect(other.locator('.top-x-publication-views').first).to_have_text('321 Views')
        expect(other.locator('.top-x-publication-views').nth(1)).to_have_text('987 Views')
        expect(other.locator('details summary')).to_contain_text('1 frühere Veröffentlichung')
        other.locator('details summary').click()
        expect(other.locator('details time')).to_be_visible()
        assert len(writes)==0
        source.locator('button').click()
        expect(page.locator('#toasts')).to_contain_text('Testfehler')
        expect(source.locator('button')).to_be_enabled()
        fail['enabled']=False
        other.locator('button').click()
        expect(other.locator('button')).to_be_disabled()
        expect(other.locator('button')).to_have_text('Bereits frei zum Verplanen')
        expect(source.locator('button')).to_be_enabled()
        assert len(writes)==2
        assert writes[0][1]=='1' and writes[1][1]=='4'
        expect(page.get_by_role('status')).to_contain_text('01:00')
        expect(page.get_by_role('status')).to_contain_text('1 Posts ohne')
        assert page.locator('.top-x-text script').count()==0
        page.screenshot(path='/private/tmp/top-posts-x-desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path='/private/tmp/top-posts-x-mobile.png',full_page=True)
        assert not errors,errors
        assert not unexpected,unexpected
        print('Top Posts X UI OK: channel tables, publication history, age colors, disabled states, release/error/retry, escaping and mobile layout; all API data simulated')
        browser.close()


if __name__=='__main__':
    main()
