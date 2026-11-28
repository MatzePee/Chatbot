import { api } from './api.js?v=persona-hashtags-20261121-1'
import { h, field, modal, guard } from './ui.js?v=persona-hashtags-20261121-1'

import { retouchEditor, zoomable } from './retouch.js?v=grain-preview-1'

const RATIOS = [['free','Frei',null],['original','Original',null],['3:4','3:4',.75],['4:5','4:5',.8],['1:1','1:1',1],['1.91:1','1,91:1',1.91]]

export async function openImageEditor(assetOrId, { platform = null, onSaved = () => {} } = {}) {
  let asset = await api.mediaDetail(typeof assetOrId === 'string' ? assetOrId : assetOrId.id)
  if (!['image/jpeg','image/png','image/webp'].includes(asset.mime)) throw new Error('Der Bildeditor unterstützt JPEG, PNG und WebP.')
  const original = new Image()
  const sourceEdit=(platform==='instagram' && asset.image_crops?.variants?.instagram) || asset.image_crops?.variants?.all
  const mediaPath='/api/v1/media/'+asset.id
  const sourcePath=sourceEdit?.settings?.retouched ? mediaPath+'/edit-source/'+sourceEdit.id : mediaPath+'/file?variant=original'
  original.src=window.CreatorStudioCharacters?.url(sourcePath) || sourcePath
  await original.decode()
  const base = document.createElement('canvas'), scale = Math.min(1,1600/Math.max(original.naturalWidth,original.naturalHeight))
  base.width=Math.round(original.naturalWidth*scale);base.height=Math.round(original.naturalHeight*scale)
  base.getContext('2d').drawImage(original,0,0,base.width,base.height)
  let state={rotation:0,flip_x:false,flip_y:false,straighten:0},rect,ratio=null,preset='original',drag=null,transformed
  let busy=false
  const scope=h('select',{'aria-label':'Zuschnitt speichern für'},h('option',{value:'instagram',selected:platform==='instagram'},'Nur Instagram-Kanäle'),h('option',{value:'all',selected:platform!=='instagram'},'Alle Kanäle'))
  const canvas=h('canvas',{tabIndex:0,'aria-label':'Bildausschnitt verschieben oder an den Ecken verkleinern. Pfeiltasten verschieben den Ausschnitt.',style:{display:'block',width:'100%',height:'auto',maxWidth:'100%',touchAction:'none',cursor:'move',borderRadius:'10px',background:'#1e293b'}})
  const stage=zoomable(canvas, e => !cropCorner(e)).frame
  const status=h('div',{role:'status','aria-live':'polite',style:{color:'#475569',fontSize:'13px'}})
  const format=h('div',{class:'row',style:{gap:'8px'}})
  const angle=h('input',{type:'range',min:-15,max:15,step:.1,value:0,'aria-label':'Gerade richten'})
  const angleText=h('span',{},'0°')
  const save=h('button',{class:'primary'},'Bearbeitung speichern')
  const restore=h('button',{},'Gespeicherte Bearbeitung entfernen')
  let activeTool='crop', baseline='', committed=false
  const labels={crop:'Zuschnitt',enhance:'Verbessern',erase:'KI Radierer',inpaint:'Inpainting',krea:'Inpainting (Krea3 BigLove Edit3)',full:'Qwen ohne Maske',outpaint:'Outpainting'}
  const toolRow=h('div',{class:'row',role:'tablist','aria-label':'Bildwerkzeuge',style:{borderBottom:'1px solid var(--line)',paddingBottom:'6px',flexWrap:'wrap'}})
  const editor=retouchEditor(original,asset.id,photo=>{
    const factor=Math.min(1,1600/Math.max(photo.width,photo.height))
    const width=Math.round(photo.width*factor),height=Math.round(photo.height*factor)
    const resized=base.width!==width || base.height!==height
    base.width=width;base.height=height
    base.getContext('2d').drawImage(photo,0,0,width,height)
    if(resized){ratio=null;preset='original'}
    transform(resized)
  },value=>{busy=value;toolRow.inert=value;editor.viewport.inert=editor.panel.inert=value;restore.disabled=value||!asset.image_crops?.variants?.[scope.value];paint()})
  editor.panel.setAttribute('role','tabpanel')
  editor.viewport.style.display=editor.panel.style.display='none'
  function decide(title,message,saveLabel){
    return new Promise(resolve=>{
      modal(title,close=>{
        const choose=value=>{resolve(value);close()}
        return h('div',{class:'col'},h('p',{},message),h('div',{class:'row'},
          h('button',{class:'primary',onClick:()=>choose('save')},saveLabel),
          h('button',{onClick:()=>choose('discard')},'Verwerfen'),
          h('button',{onClick:()=>choose('stay')},'Weiter bearbeiten')))
      },{beforeClose:()=>{resolve('stay');return true}})
    })
  }
  for(const [tool,label] of Object.entries(labels)){
    const button=h('button',{type:'button',role:'tab','aria-selected':String(tool===activeTool),
      tabIndex:tool===activeTool?0:-1,class:tool===activeTool?'primary':''},label)
    toolRow.append(button)
    button.onclick=guard(async()=>{
      if(busy || activeTool===tool)return
      if(editor.pending()){
        const answer=await decide('Werkzeug wechseln?','Die aktuelle Auswahl oder Eingabe wurde noch nicht angewendet.','Erst anwenden')
        if(answer==='stay')return
        if(answer==='save')await editor.applyPending()
      }
      editor.discardDraft()
      const cropping=tool==='crop'
      if(!cropping)editor.open(tool)
      activeTool=tool
      editor.panel.setAttribute('aria-label',label)
      for(const btn of toolRow.children){
        btn.setAttribute('aria-selected',String(btn===button))
        btn.tabIndex=btn===button?0:-1
        btn.className=btn===button?'primary':''
      }
      editor.viewport.style.display=editor.panel.style.display=cropping?'none':''
      cropControls.style.display=stage.style.display=status.style.display=cropping?'':'none'
    })
    button.onkeydown=e=>{
      const tabs=[...toolRow.children],at=tabs.indexOf(button)
      const next={ArrowLeft:(at+tabs.length-1)%tabs.length,ArrowRight:(at+1)%tabs.length,Home:0,End:tabs.length-1}[e.key]
      if(next===undefined)return
      e.preventDefault();tabs[next].focus();tabs[next].click()
    }
  }
  const snapshot=()=>JSON.stringify([state,rect,scope.value,canvas.width,canvas.height])
  const dirty=()=>!committed && (editor.pending() || editor.patches.length || snapshot()!==baseline)
  const leaving=e=>{if(dirty()){e.preventDefault();e.returnValue=''}}
  let closeDialog
  const center=(r)=>{rect={x:(canvas.width-r.w)/2,y:(canvas.height-r.h)/2,w:r.w,h:r.h}}
  function fit(){
    const aspect=ratio || canvas.width/canvas.height
    let w=canvas.width,h=w/aspect
    if(h>canvas.height){h=canvas.height;w=h*aspect}
    center({w,h});paint()
  }
  function transform(reset=true){
    const quarter=state.rotation%360,turned=document.createElement('canvas')
    turned.width=quarter%180?base.height:base.width;turned.height=quarter%180?base.width:base.height
    let context=turned.getContext('2d');context.translate(turned.width/2,turned.height/2);context.scale(state.flip_x?-1:1,state.flip_y?-1:1);context.rotate(quarter*Math.PI/180);context.drawImage(base,-base.width/2,-base.height/2)
    const rad=state.straighten*Math.PI/180,output=document.createElement('canvas')
    output.width=Math.ceil(Math.abs(turned.width*Math.cos(rad))+Math.abs(turned.height*Math.sin(rad)))
    output.height=Math.ceil(Math.abs(turned.width*Math.sin(rad))+Math.abs(turned.height*Math.cos(rad)))
    context=output.getContext('2d');context.fillStyle='white';context.fillRect(0,0,output.width,output.height);context.translate(output.width/2,output.height/2);context.rotate(rad);context.drawImage(turned,-turned.width/2,-turned.height/2)
    transformed=output;canvas.width=output.width;canvas.height=output.height
    if(reset)fit();else paint()
  }
  function paint(){
    if(!rect||!transformed)return
    const context=canvas.getContext('2d');context.clearRect(0,0,canvas.width,canvas.height);context.drawImage(transformed,0,0)
    context.fillStyle='rgba(15,23,42,.62)';context.fillRect(0,0,canvas.width,canvas.height)
    context.save();context.beginPath();context.rect(rect.x,rect.y,rect.w,rect.h);context.clip();context.drawImage(transformed,0,0);context.restore()
    const unit=canvas.width/Math.max(canvas.getBoundingClientRect().width,300)
    context.lineWidth=2*unit;context.strokeStyle='#fff';context.strokeRect(rect.x,rect.y,rect.w,rect.h)
    context.lineWidth=unit;context.strokeStyle='rgba(255,255,255,.55)'
    for(let n=1;n<3;n++){context.beginPath();context.moveTo(rect.x+rect.w*n/3,rect.y);context.lineTo(rect.x+rect.w*n/3,rect.y+rect.h);context.moveTo(rect.x,rect.y+rect.h*n/3);context.lineTo(rect.x+rect.w,rect.y+rect.h*n/3);context.stroke()}
    context.fillStyle='#0071e3';for(const [x,y]of[[rect.x,rect.y],[rect.x+rect.w,rect.y],[rect.x,rect.y+rect.h],[rect.x+rect.w,rect.y+rect.h]])context.fillRect(x-6*unit,y-6*unit,12*unit,12*unit)
    const aspect=rect.w/rect.h,valid=aspect>=.75-0.001&&aspect<=1.911
    status.style.color=valid?'#17683c':'#80520b';status.textContent=`Ausschnitt ${aspect.toFixed(2)}:1 · ${valid?'Instagram-Format passt':'⚠ Für Instagram zwischen 3:4 und 1,91:1 zuschneiden'}`
    save.disabled=busy||(scope.value==='instagram'&&!valid)
    for(const button of format.children){const active=button.dataset.preset===preset;button.setAttribute('aria-pressed',String(active));button.style.borderColor=active?'#0071e3':'';button.style.background=active?'#eaf3ff':'';button.style.color=active?'#075ba6':''}
  }
  function loadSaved(){
    const saved=asset.image_crops?.variants?.[scope.value] || (scope.value==='instagram'?asset.image_crops?.variants?.all:null)
    const config=saved?.settings
    state=config?{rotation:config.rotation||0,flip_x:!!config.flip_x,flip_y:!!config.flip_y,straighten:config.straighten||0}:{rotation:0,flip_x:false,flip_y:false,straighten:0}
    angle.value=state.straighten;angleText.textContent=state.straighten+'°';ratio=null;preset='original';transform()
    if(config){rect={x:config.x*canvas.width,y:config.y*canvas.height,w:config.width*canvas.width,h:config.height*canvas.height};ratio=rect.w/rect.h;preset='free';paint()}
    restore.disabled=!asset.image_crops?.variants?.[scope.value]
  }
  for(const[key,label,aspect]of RATIOS){format.appendChild(h('button',{type:'button',dataset:{preset:key},onClick:()=>{preset=key;ratio=key==='original'?canvas.width/canvas.height:aspect;if(key==='free'){paint();return}fit()}},label))}
  const point=e=>{const bounds=canvas.getBoundingClientRect();return{x:(e.clientX-bounds.left)*canvas.width/bounds.width,y:(e.clientY-bounds.top)*canvas.height/bounds.height}}
  function cropCorner(e){
    const p=point(e),hit=18*canvas.width/canvas.getBoundingClientRect().width
    const corners=[[rect.x,rect.y,-1,-1],[rect.x+rect.w,rect.y,1,-1],[rect.x,rect.y+rect.h,-1,1],[rect.x+rect.w,rect.y+rect.h,1,1]]
    return corners.find(([x,y])=>Math.hypot(x-p.x,y-p.y)<hit)
  }
  canvas.addEventListener('pointerdown',e=>{
    if(busy || e.button!==0)return
    e.preventDefault();canvas.setPointerCapture(e.pointerId)
    const p=point(e),corner=cropCorner(e)
    drag=corner?{kind:'resize',sx:corner[2],sy:corner[3],anchorX:corner[2]<0?rect.x+rect.w:rect.x,anchorY:corner[3]<0?rect.y+rect.h:rect.y}:{kind:'move',start:p,rect:{...rect}}
  })
  canvas.addEventListener('pointermove',e=>{
    if(!drag)return;e.preventDefault();const p=point(e)
    if(drag.kind==='move'){rect.x=Math.max(0,Math.min(canvas.width-rect.w,drag.rect.x+p.x-drag.start.x));rect.y=Math.max(0,Math.min(canvas.height-rect.h,drag.rect.y+p.y-drag.start.y))}
    else{
      const maxW=drag.sx<0?drag.anchorX:canvas.width-drag.anchorX,maxH=drag.sy<0?drag.anchorY:canvas.height-drag.anchorY
      let w=Math.max(16,Math.min(maxW,(p.x-drag.anchorX)*drag.sx)),hh=Math.max(16,Math.min(maxH,(p.y-drag.anchorY)*drag.sy))
      if(ratio){hh=w/ratio;if(hh>maxH){hh=maxH;w=hh*ratio}}
      rect={x:drag.sx<0?drag.anchorX-w:drag.anchorX,y:drag.sy<0?drag.anchorY-hh:drag.anchorY,w,h:hh}
    }paint()
  })
  for(const type of ['pointerup','pointercancel'])canvas.addEventListener(type,()=>{drag=null})
  canvas.addEventListener('keydown',e=>{const delta=e.shiftKey?10:1;if(e.key==='ArrowLeft')rect.x=Math.max(0,rect.x-delta);else if(e.key==='ArrowRight')rect.x=Math.min(canvas.width-rect.w,rect.x+delta);else if(e.key==='ArrowUp')rect.y=Math.max(0,rect.y-delta);else if(e.key==='ArrowDown')rect.y=Math.min(canvas.height-rect.h,rect.y+delta);else return;e.preventDefault();paint()})
  angle.addEventListener('input',()=>{state.straighten=Number(angle.value);angleText.textContent=state.straighten+'°';transform()})
  scope.addEventListener('change',()=>{restore.disabled=!asset.image_crops?.variants?.[scope.value];paint()})
  async function submit(reset){
    if(busy)return
    if(!reset && editor.pending())await editor.applyPending()
    let failed=false
    busy=true;toolRow.inert=true;editor.viewport.inert=editor.panel.inert=true;save.disabled=true;restore.disabled=true;status.textContent='Speichert …'
    try{
      const expected=asset.image_crops?.variants?.[scope.value]?.id || ''
      asset=await api.saveImageCrop(asset.id,{scope:scope.value,expected_version:expected,source_version:sourceEdit?.id||null,patches:reset?[]:editor.patches,reset,...state,x:rect.x/canvas.width,y:rect.y/canvas.height,width:rect.w/canvas.width,height:rect.h/canvas.height})
      window.dispatchEvent(new CustomEvent('creatorstudio:media-edited',{detail:asset}))
      committed=true;await onSaved(asset);busy=false
    }catch(error){failed=true;status.textContent=error.message;status.style.color='#b42332';throw error}
    finally{busy=false;toolRow.inert=false;editor.viewport.inert=editor.panel.inert=false;if(!failed)paint();else save.disabled=false;restore.disabled=!asset.image_crops?.variants?.[scope.value]}
  }
  save.onclick=guard(async()=>{await submit(false);await closeDialog()})
  restore.onclick=guard(async()=>{
    if(dirty()){
      const answer=await decide('Gespeicherte Variante entfernen?', 'Beim Entfernen gehen deine offenen Änderungen verloren.', 'Änderungen speichern')
      if(answer==='stay')return
      await submit(answer==='discard')
    }else await submit(true)
    await closeDialog()
  })
  const cropControls=h('section',{class:'col',role:'tabpanel','aria-label':'Zuschnitt'},format,
    h('div',{class:'row'},
      h('button',{title:'90° nach links drehen',onClick:()=>{state.rotation=(state.rotation+270)%360;transform()}},'↶ Links'),
      h('button',{title:'90° nach rechts drehen',onClick:()=>{state.rotation=(state.rotation+90)%360;transform()}},'↷ Rechts'),
      h('button',{onClick:()=>{state.flip_x=!state.flip_x;transform()}},'↔ Spiegeln'),
      h('button',{onClick:()=>{state.flip_y=!state.flip_y;transform()}},'↕ Spiegeln')),
    field('Gerade richten',h('div',{class:'row'},angle,angleText)),
    h('div',{class:'row'},h('button',{onClick:()=>{center(rect);paint()}},'Zentrieren'),h('button',{onClick:()=>{state={rotation:0,flip_x:false,flip_y:false,straighten:0};angle.value=0;angleText.textContent='0°';ratio=null;preset='original';transform()}},'Zurücksetzen')))
  modal(`Bild bearbeiten — ${asset.filename}`,close=>{closeDialog=close;return h('div',{class:'col image-crop-editor',style:{gap:'16px',minWidth:'0'}},
    stage,editor.viewport,toolRow,status,cropControls,editor.panel,
    field('Bearbeitung speichern für',scope,'„Alle Kanäle“ ersetzt auch einen bisherigen Instagram-Zuschnitt. „Nur Instagram“ lässt die gemeinsame Variante unverändert.'),
    h('p',{class:'hint'},'Das Original bleibt erhalten. Der Zuschnitt gilt für die Bibliothek und noch nicht versendete Beiträge. Bereits veröffentlichte oder an einen Anbieter übergebene Beiträge ändern sich nicht.'),
    h('div',{class:'row'},save,restore,h('button',{onClick:()=>{if(!busy)close()}},'Abbrechen')))
  },{wide:true,beforeClose:async()=>{
    if(busy)return false
    if(dirty()){
      const answer=await decide('Ungespeicherte Änderungen','Möchtest du die Bearbeitung vor dem Schließen speichern?','Speichern')
      if(answer==='stay')return false
      if(answer==='save')await submit(false)
    }
    window.removeEventListener('beforeunload',leaving)
    return true
  }})
  loadSaved();baseline=snapshot()
  window.addEventListener('beforeunload',leaving)
}
