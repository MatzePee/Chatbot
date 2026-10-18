import io
import json
from email.parser import BytesParser
from email.policy import default
from PIL import Image
import httpx
import pytest
from autoposter.adapters import get_adapter, AdapterError
from autoposter.adapters.base import Credentials
from autoposter.models import Channel, Post, MediaAsset
from .test_telegram import no_network

def image_bytes():
    out = io.BytesIO(); Image.new('RGB',(16,16),'blue').save(out,format='JPEG'); return out.getvalue()

def credential():
    return Credentials('123:synthetic', external_user_id='123', raw_meta={'destination':'@sample_channel','chat_id':'-100456','username':'sample_channel'})

@pytest.mark.asyncio
@pytest.mark.parametrize('types,method', [([], 'sendMessage'),(['image/jpeg'],'sendPhoto'),(['video/mp4'],'sendVideo'),(['image/jpeg','video/mp4'],'sendMediaGroup'),(['image/jpeg']*10,'sendMediaGroup')])
async def test_publish_exact_protocol_and_all_ids(monkeypatch, types, method):
    adapter=get_adapter('telegram'); refs=[]
    for i,mime in enumerate(types):
        asset=MediaAsset(id=__import__('uuid').uuid4(),filename=f'media{i}',mime=mime,width=16,height=16)
        refs.append(await adapter.upload_media(credential(),asset,image_bytes() if mime.startswith('image/') else b'synthetic-mp4'))
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.method == 'POST' and str(req.url) == f'https://api.telegram.org/bot123:synthetic/{method}'
        if types:
            msg=BytesParser(policy=default).parsebytes(b'Content-Type: '+req.headers['content-type'].encode()+b'\r\n\r\n'+req.content)
            parts={part.get_param('name',header='content-disposition'):part.get_payload(decode=True) for part in msg.iter_parts()}
            assert parts['chat_id']==b'-100456' and parts['allow_paid_broadcast']==b'false'
            if len(types)>1:
                media=json.loads(parts['media'])
                assert len(media)==len(types) and media[0]['caption']=='<literal> hello'
                assert all('caption' not in x for x in media[1:])
                for i,item in enumerate(media):
                    assert item['media']==f'attach://media{i}'
                    assert item['type']==('photo' if types[i].startswith('image/') else 'video')
                    assert parts[f'media{i}']==(image_bytes() if types[i].startswith('image/') else b'synthetic-mp4')
            else:
                assert parts['caption']==b'<literal> hello'
                assert parts['photo' if types[0].startswith('image/') else 'video']
        else:
            assert json.loads(req.content)=={'chat_id':'-100456','text':'<literal> hello','allow_paid_broadcast':False}
        result=[{'message_id':90+i,'chat':{'id':-100456}} for i in range(max(1,len(types)))]
        return httpx.Response(200,json={'ok':True,'result':result if len(types)>1 else result[0]})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out=await adapter.publish(credential(),Channel(platform='telegram',handle='@sample_channel'),Post(body_text='<literal> hello'),refs)
    assert len(calls)==1
    assert out.raw=={'chat_id':'-100456','message_ids':list(range(90,90+max(1,len(types))))}
    assert out.external_post_id==','.join(str(x) for x in out.raw['message_ids'])
    assert out.external_url=='https://t.me/sample_channel/90'

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['long_text','long_caption','album11','oversize_photo','invalid_mime','wide_photo','missing_binding','wrong_chat','partial_album','timeout'])
async def test_publish_rejects_without_silent_loss(monkeypatch,mode):
    adapter=get_adapter('telegram'); cred=credential(); refs=[]; seen=[]
    def handler(req):
        seen.append(req)
        if mode=='timeout':raise httpx.ReadTimeout(str(req.url),request=req)
        return httpx.Response(200,json={'ok':True,'result': [{'message_id':3,'chat':{'id':-100456}}] if mode=='partial_album' else {'message_id':3,'chat':{'id':-100999}}})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    text='hello'
    with pytest.raises(AdapterError) as error:
        if mode=='long_text':text='🙂'*2049  # conservative UTF-16 units
        if mode=='missing_binding':cred.raw_meta={}
        if mode in ['long_caption','album11','partial_album']:
            refs=[await adapter.upload_media(cred,MediaAsset(id=__import__('uuid').uuid4(),filename='p.jpg',mime='image/jpeg',width=16,height=16),image_bytes()) for _ in range(11 if mode=='album11' else 2)]
            if mode=='long_caption':text='x'*1025
        if mode in ['oversize_photo','invalid_mime','wide_photo']:
            refs=[await adapter.upload_media(cred,MediaAsset(id=__import__('uuid').uuid4(),filename='p.jpg',mime='image/gif' if mode=='invalid_mime' else 'image/jpeg',width=16,height=16),b'x'*10_000_001 if mode=='oversize_photo' else image_bytes() if mode!='wide_photo' else _wide())]
        await adapter.publish(cred,Channel(platform='telegram',handle='@sample_channel'),Post(body_text=text),refs)
    assert '123:synthetic' not in str(error.value)
    assert not error.value.retryable
    assert len(seen)==(1 if mode in ['wrong_chat','partial_album','timeout'] else 0)

def _wide():
    out=io.BytesIO();Image.new('RGB',(500,10)).save(out,format='JPEG');return out.getvalue()
