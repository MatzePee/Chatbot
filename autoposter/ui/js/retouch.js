import { h } from './ui.js?v=persona-hashtags-20261121-1'

import { matchMaskBoundary } from './mask-blend.js?v=1'

// Keep the image point under the wheel stationary in viewport coordinates.
export function zoomable(sheet, panWithLeft=null, panWithRight=()=>true) {
  const frame=h('div',{style:{overflow:'hidden',maxWidth:'650px',width:'100%',margin:'auto',position:'relative'}},sheet)
  sheet.style.transformOrigin='0 0'
  let zoom=1, left=0, top=0, pan=false, drag=null
  function move(x,y){
    left=Math.max(frame.clientWidth*(1-zoom),Math.min(0,x))
    top=Math.max(frame.clientHeight*(1-zoom),Math.min(0,y))
    sheet.style.transform='translate('+left+'px,'+top+'px) scale('+zoom+')'
  }
  function scale(factor,x,y) {
    const next=Math.max(1,Math.min(12,zoom*factor)), k=next/zoom
    zoom=next
    move(x-(x-left)*k,y-(y-top)*k)
  }
  frame.addEventListener('wheel',e=>{
    e.preventDefault()
    const r=frame.getBoundingClientRect()
    scale(Math.exp(-Math.sign(e.deltaY)*0.18),e.clientX-r.left,e.clientY-r.top)
  },{passive:false})
  frame.addEventListener('contextmenu',e=>{
    if(zoom>1)e.preventDefault()
  })
  frame.addEventListener('pointerdown',e=>{
    const mousePan=zoom>1 && e.pointerType==='mouse' &&
      ((e.button===2 && panWithRight(e)) || (e.button===0 && panWithLeft?.(e)))
    if(drag || !(mousePan || (pan && e.button===0)))return
    e.preventDefault();e.stopPropagation()
    frame.setPointerCapture(e.pointerId)
    drag={id:e.pointerId,x:e.clientX-left,y:e.clientY-top}
  },true)
  frame.addEventListener('pointermove',e=>{
    if(drag?.id===e.pointerId)move(e.clientX-drag.x,e.clientY-drag.y)
  })
  frame.addEventListener('lostpointercapture',()=>{drag=null})
  return {frame,setPan:value=>{pan=value},step:factor=>scale(factor,frame.clientWidth/2,frame.clientHeight/2)}
}

export function retouchEditor(original,assetId,onChange,onBusy){
  const photo=h('canvas',{style:{display:'block',width:'100%'}})
  const mask=h('canvas',{style:{position:'absolute',inset:0,width:'100%',height:'100%',opacity:'.55',touchAction:'none',cursor:'crosshair'}})
  const cursor=h('span',{hidden:true,style:{position:'absolute',pointerEvents:'none',border:'1px solid white',boxShadow:'0 0 0 1px #333',borderRadius:'100%',transform:'translate(-50%,-50%)'}})
  const sheet=h('div',{style:{position:'relative'}},photo,mask,cursor)
  const zoom=zoomable(sheet,null,e=>e.target!==mask)
  const size=h('input',{type:'range',min:2,max:200,value:40,'aria-label':'Pinselgröße'})
  const sizeValue=h('output',{},'40 px')
  size.oninput=()=>sizeValue.textContent=size.value+' px'
  const paint=h('button',{},'Markieren'), subtract=h('button',{},'Maske abziehen')
  const undo=h('button',{},'Rückgängig'), clear=h('button',{},'Maske leeren'), run=h('button',{class:'primary'},'Markiertes radieren')
  const info=h('div',{role:'status'},'Übermale das Objekt einschließlich seiner Ränder.')
  let operation='erase', draftChanged=false
  const instruction=h('textarea',{rows:3,maxLength:4000,placeholder:'Was soll sich im markierten Bereich ändern?'})
  const purpose=h('select',{'aria-label':'Inpainting-Aufgabe'},
    h('option',{value:'edit'},'Bereich ändern'),h('option',{value:'remove'},'Objekt entfernen'))
  const purposeLabel=h('label',{class:'col'},'Inpainting-Aufgabe',purpose)
  const resolution=h('select',{'aria-label':'KI-Auflösung'},
    h('option',{value:2048},'Hohe Details · bis 2048 px'),h('option',{value:1536},'Mittel · bis 1536 px'),
    h('option',{value:1024},'Schnell · bis 1024 px'))
  const protect=h('input',{type:'range',min:0,max:90,step:1,value:0,'aria-label':'Oberen Bildteil schützen'})
  const protectedSize=h('output',{},'0 %')
  const boundary=h('div',{style:{position:'absolute',left:0,right:0,top:0,pointerEvents:'none',borderBottom:'2px dashed white',background:'rgba(20,30,45,.15)'}})
  sheet.append(boundary)
  const actualSize=h('output',{})
  const fullOptions=h('div',{class:'col'},h('label',{class:'row'},'KI-Auflösung',resolution,actualSize),
    h('label',{class:'row'},'Oberen Bildteil schützen',protect,protectedSize))
  function fullRegion(){
    const y=Math.min(photo.height-1,Math.round(photo.height*Number(protect.value)/100))
    return {x:0,y,w:photo.width,h:photo.height-y}
  }
  function fullSize(w,h){
    const scale=Math.min(1,Number(resolution.value)/Math.max(w,h))
    return [w,h].map(v=>Math.max(32,Math.round(v*scale/32)*32))
  }
  function updateFullOptions(){
    const active=operation==='full'
    const krea=operation==='krea'
    fullOptions.style.display=active||krea?'':'none'
    protect.parentElement.style.display=krea?'none':''
    boundary.style.display=active && Number(protect.value)>0?'':'none'
    boundary.style.height=protect.value+'%'
    protectedSize.textContent=protect.value+' %'
    const r=krea?{w:photo.width,h:photo.height}:fullRegion()
    actualSize.textContent=fullSize(r.w,r.h).join(' × ')+' px'
  }
  protect.oninput=resolution.onchange=updateFullOptions
  const enhanceType=h('select',{'aria-label':'Verbesserung'},
    ...Object.entries({face:'Gesicht detaillieren',hands:'Hände detaillieren',feet:'Füße detaillieren',grain:'Bildrauschen hinzufügen'})
      .map(([value,text])=>h('option',{value},text)))
  const steps=h('input',{type:'number',min:1,max:100,step:1,value:3})
  const denoise=h('input',{type:'number',min:0,max:1,step:.01,value:.25})
  const grain=h('input',{type:'range',min:1,max:64,value:12})
  const amount=h('output',{},'12')
  let grainDraft=null, grainFrame=0
  function renderGrain(){
    const ctx=photo.getContext('2d')
    if(!grainDraft){
      const before=ctx.getImageData(0,0,photo.width,photo.height)
      grainDraft={before,after:ctx.createImageData(before.width,before.height),
        seed:crypto.getRandomValues(new Uint32Array(1))[0],strength:null}
    }
    const {before,after,seed}=grainDraft, strength=Number(grain.value)
    if(grainDraft.strength===strength)return
    for(let offset=0;offset<before.data.length;offset+=4){
      let v=(offset/4+seed)>>>0
      v=Math.imul(v^(v>>>16),0x7feb352d);v=Math.imul(v^(v>>>15),0x846ca68b);v^=v>>>16
      const delta=Math.floor((((v&255)+((v>>>8)&255)-255)*strength+127)/255)
      for(let c=0;c<3;c++)after.data[offset+c]=before.data[offset+c]+delta
      after.data[offset+3]=before.data[offset+3]
    }
    ctx.putImageData(after,0,0)
    grainDraft.strength=strength
  }
  function discardGrain(){
    cancelAnimationFrame(grainFrame);grainFrame=0
    if(grainDraft)photo.getContext('2d').putImageData(grainDraft.before,0,0)
    grainDraft=null
  }
  grain.oninput=()=>{
    amount.textContent=grain.value
    comparison.hidden=true;compare.textContent='Original anzeigen'
    if(!grainFrame)grainFrame=requestAnimationFrame(()=>{grainFrame=0;renderGrain()})
  }
  const sampling=h('div',{class:'row'},h('label',{},'Steps ',steps),h('label',{},'Denoising ',denoise))
  const grainControl=h('label',{class:'row'},'Rauschstärke',grain,amount)
  const enhanceControls=h('div',{class:'col'},enhanceType,sampling,grainControl)
  const strengths={face:.25,hands:.35,feet:.4}
  function showEnhancement(){
    const noise=enhanceType.value==='grain'
    sampling.style.display=noise?'none':''
    grainControl.style.display=noise?'':'none'
    run.textContent=noise?'Bildrauschen übernehmen':enhanceType.selectedOptions[0].textContent
    info.textContent=noise?'Regler bewegen, um das Bildrauschen im Bild zu sehen.':'Der Detektor sucht passende Bildbereiche automatisch.'
  }
  enhanceType.onchange=()=>{
    discardGrain()
    if(enhanceType.value in strengths)denoise.value=strengths[enhanceType.value]
    showEnhancement();refresh()
  }
  const removing=()=>operation==='inpaint' && purpose.value==='remove'
  function explainInpaint(){
    instruction.placeholder=removing()?'Optional: Wie sieht der Hintergrund hinter dem Objekt aus?':'Was soll sich im markierten Bereich ändern?'
    info.textContent=removing()?'Markiere das ganze Objekt mit etwas Rand und seinem Schatten. Spare Personen und andere Details aus, die bleiben sollen.':'Übermale den Bereich, den du ändern möchtest.'
    run.textContent=removing()?'Objekt entfernen':'Änderung anwenden'
  }
  purpose.onchange=()=>{explainInpaint();refresh()}
  const promptLabel=h('label',{class:'col'},'Bildanweisung',instruction)
  const sides={}
  const dimensions=h('output',{})
  const preview=h('canvas',{style:{display:'block',maxWidth:'100%',maxHeight:'50vh',margin:'auto'}})
  const paddingControls=h('div',{class:'col'},h('strong',{},'Erweiterung in Pixeln'))
  for(const [key,label] of Object.entries({left:'Links',right:'Rechts',top:'Oben',bottom:'Unten'})){
    sides[key]=h('input',{type:'number',min:0,max:4096,step:1,value:0,style:{width:'7em'}})
    paddingControls.append(h('label',{class:'row'},label,sides[key]))
    sides[key].oninput=()=>{draftChanged=true;previewPadding();refresh()}
  }
  paddingControls.append(dimensions)
  instruction.oninput=()=>{draftChanged=true;refresh()}
  function padding(){
    return Object.fromEntries(Object.entries(sides).map(([key,input])=>[key,input.valueAsNumber]))
  }
  function previewPadding(){
    const pad=padding()
    const w=photo.width+pad.left+pad.right, h=photo.height+pad.top+pad.bottom
    const valid=Object.values(sides).every(input=>input.value!=='' && input.checkValidity()) && w*h<=24000000
    dimensions.textContent=valid?w+' × '+h+' px':'Bitte gültige Ränder und höchstens 24 Megapixel wählen.'
    if(!valid)return false
    const scale=Math.min(600/w,600/h,1)
    preview.width=Math.max(1,Math.round(w*scale));preview.height=Math.max(1,Math.round(h*scale))
    const ctx=preview.getContext('2d')
    ctx.fillStyle='#baa8d4';ctx.fillRect(0,0,preview.width,preview.height)
    ctx.drawImage(photo,pad.left*scale,pad.top*scale,photo.width*scale,photo.height*scale)
    return Object.values(pad).some(v=>v>0)
  }
  const ink=mask.getContext('2d',{willReadFrequently:true})
  const patches=[]
  let busy=false, mode='paint', strokes=[], active=null, hasInk=false, pointer=null, previous=null
  function refresh(){
    const ready=operation==='enhance'?true:operation==='full'?instruction.value.trim():operation==='outpaint'?previewPadding():hasInk && (!['krea','inpaint'].includes(operation)||removing()||instruction.value.trim())
    run.disabled=busy||!ready
    undo.disabled=busy||(!strokes.length&&!previous)
    clear.disabled=busy||!strokes.length
    paint.setAttribute('aria-pressed',String(mode==='paint'))
    subtract.setAttribute('aria-pressed',String(mode==='subtract'))
  }
  const pan=h('button',{},'Ansicht verschieben')
  function setMode(value){
    mode=value;zoom.setPan(value==='pan');pan.setAttribute('aria-pressed',String(value==='pan'));refresh()
  }
  pan.onclick=()=>setMode('pan')
  paint.onclick=()=>setMode('paint')
  subtract.onclick=()=>setMode('subtract')
  function point(e){
    const r=mask.getBoundingClientRect(), x=(e.clientX-r.left)/r.width, y=(e.clientY-r.top)/r.height
    Object.assign(cursor.style,{left:x*100+'%',top:y*100+'%',width:size.value/mask.width*100+'%',height:size.value/mask.height*100+'%'})
    cursor.hidden=false
    return [x*mask.width,y*mask.height]
  }
  function resetMask(){
    ink.clearRect(0,0,mask.width,mask.height)
    strokes=[];active=null;pointer=null;hasInk=false;cursor.hidden=true;refresh()
  }
  function segment(stroke,a,b) {
    ink.globalCompositeOperation=stroke.mode==="paint"?"source-over":"destination-out";
    ink.lineWidth=stroke.size; ink.lineCap="round"; ink.strokeStyle=ink.fillStyle="#be87ff";
    ink.beginPath();
    if (!b) { ink.arc(...a,stroke.size/2,0,2*Math.PI);ink.fill(); }
    else { ink.moveTo(...a);ink.lineTo(...b);ink.stroke(); }
    ink.globalCompositeOperation="source-over";
  }
  function redraw() {
    ink.clearRect(0,0,mask.width,mask.height);
    for (const s of strokes) s.points.forEach((p,i)=>i?segment(s,s.points[i-1],p):segment(s,p));
    hasInk=strokes.some(s=>s.mode==="paint");refresh();
  }
  mask.oncontextmenu=e=>e.preventDefault()
  mask.onpointerdown=e=>{
    const mouse=e.pointerType==='mouse'
    const subtracting=mouse && e.button===2
    if(busy || pointer!==null || (e.button!==0 && !subtracting))return
    const brush=mouse?(subtracting?'subtract':'paint'):mode
    if(brush==='pan' || (brush==='subtract' && !hasInk))return
    e.preventDefault()
    pointer=e.pointerId; mask.setPointerCapture(pointer);
    active={mode:brush,size:+size.value,points:[point(e)]};strokes.push(active);segment(active,active.points[0]);
  };
  mask.onpointermove=e=>{
    if(pointer!==null && e.pointerId!==pointer)return;
    const p=point(e);
    if(active){segment(active,active.points.at(-1),p);active.points.push(p);}
  };
  mask.onpointerup=mask.onpointercancel=mask.onlostpointercapture=e=>{
    if(pointer!==e.pointerId)return;
    active=null;pointer=null;hasInk=strokes.some(s=>s.mode==="paint");refresh();
  };
  mask.onpointerleave=()=>cursor.hidden=true;
  function canvas(w,h) {const c=document.createElement("canvas");c.width=w;c.height=h;return c;}
  function region() {
    // Include neighboring texture so the model can reconstruct the background.
    let left=mask.width,top=mask.height,right=0,bottom=0;
    for(const s of strokes) if(s.mode==="paint") for(const [x,y] of s.points){
      left=Math.min(left,x-s.size/2);right=Math.max(right,x+s.size/2);
      top=Math.min(top,y-s.size/2);bottom=Math.max(bottom,y+s.size/2);
    }
    const span=Math.ceil(Math.max(128,right-left,bottom-top)*1.8);
    const w=Math.min(photo.width,span),h=Math.min(photo.height,span);
    const x=Math.max(0,Math.min(photo.width-w,Math.round((left+right-w)/2)));
    const y=Math.max(0,Math.min(photo.height-h,Math.round((top+bottom-h)/2)));
    return {x,y,w,h};
  }
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  clear.onclick=resetMask
  function resize(w,h){
    photo.width=mask.width=w;photo.height=mask.height=h
  }
  undo.onclick=()=>{
    discardGrain()
    if(strokes.length){strokes.pop();redraw();return}
    if(previous){
      if(previous.expanded)resize(previous.pixels.width,previous.pixels.height)
      photo.getContext('2d').putImageData(previous.pixels,previous.x,previous.y)
      strokes=previous.strokes;previous=null;patches.pop();redraw();onChange(photo)
      info.textContent='Letzte Retusche zurückgenommen.'
    }
  }
  const blob=c=>new Promise((resolve,reject)=>c.toBlob(b=>b?resolve(b):reject(Error('PNG konnte nicht erstellt werden.'))))
  async function checked(response){
    if(!response.ok)throw Error((await response.json().catch(()=>({}))).detail || 'ComfyUI-Anfrage fehlgeschlagen.')
    return response
  }
  run.onclick=async()=>{
    comparison.hidden=true;compare.textContent='Original anzeigen'
    busy=true;onBusy(true);refresh()
    try{
      if(patches.length>=32)throw Error('Bitte die bisherigen Retuschen zuerst speichern.')
      const enhancement=operation==='enhance'
      if(enhancement && enhanceType.value==='grain'){
        cancelAnimationFrame(grainFrame);grainFrame=0
        renderGrain()
        const {before,seed,strength}=grainDraft
        previous={x:0,y:0,pixels:before,strokes}
        patches.push({kind:'grain',seed,strength});grainDraft=null
        onChange(photo);info.textContent='Bildrauschen übernommen.';return
      }
      if(enhancement && (!steps.reportValidity() || !denoise.reportValidity() ||
          !Number.isFinite(steps.valueAsNumber) || !Number.isFinite(denoise.valueAsNumber)))throw Error('Steps und Denoising prüfen.')
      const expanding=operation==='outpaint'
      const krea=operation==='krea'
      const whole=operation==='full'
      const pad=expanding?padding():null
      const {x,y,w,h}=expanding?{x:0,y:0,w:photo.width+pad.left+pad.right,h:photo.height+pad.top+pad.bottom}:whole?fullRegion():(krea||enhancement)?{x:0,y:0,w:photo.width,h:photo.height}:region()
      const scale=1024/Math.max(w,h)
      const unit=operation==='erase'?8:32
      const img=(whole||krea||enhancement)?canvas(...fullSize(w,h)):canvas(Math.max(unit,Math.round(w*scale/unit)*unit),Math.max(unit,Math.round(h*scale/unit)*unit))
      const matte=(whole||enhancement)?null:canvas(img.width,img.height), mc=matte?.getContext('2d')
      if(expanding){
        mc.fillStyle='white';mc.fillRect(0,0,img.width,img.height)
        const pos=[pad.left*img.width/w,pad.top*img.height/h,photo.width*img.width/w,photo.height*img.height/h]
        mc.fillStyle='black';mc.fillRect(...pos)
        const input=img.getContext('2d')
        input.fillStyle='#808080';input.fillRect(0,0,img.width,img.height);input.drawImage(photo,...pos)
      } else if(whole || enhancement){
        img.getContext("2d").drawImage(photo,x,y,w,h,0,0,img.width,img.height)
      } else {
      mc.drawImage(mask,x,y,w,h,0,0,img.width,img.height)
      if(!mc.getImageData(0,0,img.width,img.height).data.some((v,i)=>i%4===3&&v))throw Error('Die Auswahl ist leer.')
      mc.globalCompositeOperation='source-in';mc.fillStyle='white';mc.fillRect(0,0,img.width,img.height)
      mc.globalCompositeOperation='destination-over';mc.fillStyle='black';mc.fillRect(0,0,img.width,img.height)
      img.getContext('2d').drawImage(photo,x,y,w,h,0,0,img.width,img.height)
      }
      const removalOriginal=removing()?img.getContext('2d').getImageData(0,0,img.width,img.height):null
      const form=new FormData()
      form.append('operation',enhancement?enhanceType.value:removing()?'remove':operation)
      if(enhancement){form.append('steps',steps.value);form.append('denoise',denoise.value)}
      const detailInput=enhancement?img.getContext('2d').getImageData(0,0,img.width,img.height):null
      form.append('prompt',operation==='erase'?'':instruction.value.trim())
      form.append('image',await blob(img),'bild.png')
      if(matte)form.append('mask',await blob(matte),'maske.png')
      const path='/api/v1/media/'+assetId+'/erase'
      info.textContent='ComfyUI bearbeitet die Auswahl …'
      const job=await (await checked(await fetch(path,{method:'POST',body:form}))).json()
      const deadline=Date.now()+15*60*1000
      for(;;){
        await delay(1400)
        if(Date.now()>deadline)throw Error('Zeitlimit erreicht. Bitte die ComfyUI-Warteschlange prüfen.')
        const result=await checked(await fetch(path+'/'+job.job,{cache:'no-store'}))
        if(!result.headers.get('content-type')?.startsWith('image/'))continue
        const url=URL.createObjectURL(await result.blob()), image=new Image()
        const ctx=img.getContext('2d')
        try{image.src=url;await image.decode();ctx.drawImage(image,0,0,img.width,img.height)}finally{URL.revokeObjectURL(url)}
        if(whole && y>0){
          const seam=ctx.createLinearGradient(0,0,0,Math.max(2,Math.min(32,img.height*.05)))
          seam.addColorStop(0,'transparent');seam.addColorStop(1,'black')
          ctx.globalCompositeOperation='destination-in';ctx.fillStyle=seam;ctx.fillRect(0,0,img.width,img.height)
        }
        if(detailInput){
          const tile=ctx.getImageData(0,0,img.width,img.height), before=detailInput.data
          let changed=false
          for(let i=0;i<before.length;i+=4){
            if(Math.max(Math.abs(tile.data[i]-before[i]),Math.abs(tile.data[i+1]-before[i+1]),Math.abs(tile.data[i+2]-before[i+2]))<=1)tile.data[i+3]=0
            else changed=true
          }
          if(!changed){info.textContent='Kein passender Bereich verbessert. Das Bild ist unverändert.';return}
          ctx.putImageData(tile,0,0)
        }
        if(removalOriginal){
          const restored=ctx.getImageData(0,0,img.width,img.height)
          const selection=mc.getImageData(0,0,img.width,img.height)
          matchMaskBoundary(removalOriginal.data,restored.data,selection.data,img.width,img.height)
          ctx.putImageData(restored,0,0)
        }
        if(!expanding && !whole && !krea && !enhancement){
          ctx.globalCompositeOperation='destination-in';ctx.drawImage(mask,x,y,w,h,0,0,img.width,img.height)
        }
        const output=photo.getContext('2d')
        previous={x,y,pixels:output.getImageData(x,y,expanding?photo.width:w,expanding?photo.height:h),strokes,expanded:expanding}
        patches.push({x,y,width:w,height:h,padding:pad,png:img.toDataURL().split(',')[1]})
        if(expanding)resize(w,h)
        output.drawImage(img,x,y,w,h)
        if(expanding){
          output.putImageData(previous.pixels,pad.left,pad.top)
          for(const input of Object.values(sides))input.value=0
        }
        resetMask();draftChanged=false;onChange(photo)
        info.textContent='Retusche fertig. Weiter markieren oder Bearbeitung speichern.';break
      }
    }catch(e){info.textContent=e.message}
    finally{busy=false;onBusy(false);refresh()}
  }
  const brushes=h('div',{class:'col'},h('span',{class:'hint'},'Maus: links markieren, rechts Maske abziehen. Für Touch den Pinselmodus wählen.'),h('div',{class:'row'},paint,subtract,pan,clear),
    h('label',{class:'row'},'Pinselgröße',size,sizeValue))
  const zoomButtons=h('div',{class:'row'},h('button',{onClick:()=>zoom.step(1.5)},'Zoom +'),h('button',{onClick:()=>zoom.step(1/1.5)},'Zoom −'))
  const comparison=h('img',{hidden:true,alt:'Original zum Vergleich',style:{position:'absolute',inset:0,width:'100%',height:'100%',objectFit:'contain',background:'#202020',pointerEvents:'none'}})
  comparison.src=original.src
  sheet.append(comparison)
  const compare=h('button',{type:'button',onClick:()=>{
    comparison.hidden=!comparison.hidden
    compare.textContent=comparison.hidden?'Original anzeigen':'Bearbeitung anzeigen'
  }},'Original anzeigen')
  const viewport=h('div',{},zoom.frame,preview)
  const panel=h('div',{class:'col'},paddingControls,enhanceControls,brushes,purposeLabel,fullOptions,promptLabel,
    h('div',{class:'row'},run,undo,compare,zoomButtons),info)
  refresh()
  return {viewport,panel,patches,
    pending:()=>strokes.length>0 || draftChanged,
    async applyPending(){
      if(run.disabled)throw Error('Bitte die Auswahl und die Bildanweisung oder die Erweiterung vervollständigen.')
      const count=patches.length
      await run.onclick()
      if(patches.length===count)throw Error(info.textContent)
    },
    discardDraft(){discardGrain();resetMask();draftChanged=false;instruction.value='';for(const input of Object.values(sides))input.value=0},
    open(tool){
    operation=tool
    enhanceControls.style.display=tool==='enhance'?'':'none'
    const expanding=tool==='outpaint'
    preview.style.display=paddingControls.style.display=expanding?'':'none'
    zoomButtons.style.display=zoom.frame.style.display=brushes.style.display=expanding?'none':''
    purposeLabel.style.display=tool==='inpaint'?'':'none'
    promptLabel.style.display=tool==='erase'?'none':''
    instruction.placeholder=expanding?'Optional: Was soll an den neuen Rändern zu sehen sein?':'Was soll sich im markierten Bereich ändern?'
    run.textContent={erase:'Markiertes radieren',inpaint:'Änderung anwenden',outpaint:'Bild erweitern'}[tool]
    info.textContent=expanding?'Lege die Ränder fest. Die Vorschau zeigt die neuen Flächen in Violett.':'Übermale den Bereich, den du ändern möchtest.'
    comparison.hidden=true
    compare.textContent='Original anzeigen'
    compare.hidden=tool!=='full' && tool!=='krea'
    mask.style.display=tool==='full'?'none':''
    if(tool==='full'){
      cursor.hidden=true;brushes.style.display='none'
      instruction.placeholder='Zum Beispiel: Färbe nur die Schuhe rot. Behalte Form, Glanz und Absätze bei.'
      run.textContent='Ganzes Bild bearbeiten'
      info.textContent='Für Schuh- oder Kleidungsänderungen kannst du den Bildteil mit dem Gesicht schützen. Qwen bearbeitet den Bereich unter der Linie.'
    }
    if(tool==='enhance'){
      brushes.style.display=mask.style.display=promptLabel.style.display='none'
      cursor.hidden=true;compare.hidden=false;showEnhancement()
    }
    if(tool==='inpaint')explainInpaint()
    if(tool==='krea'){
      run.textContent='Krea-Bearbeitung anwenden'
      info.textContent='Markiere den Bereich und beschreibe die Änderung. Im zweiten Schritt generiert Krea das gesamte Bild neu. Vergleiche das Ergebnis vor dem Speichern mit dem Original.'
    }
    if(original.naturalWidth*original.naturalHeight>24000000)throw Error('Der Radierer unterstützt Bilder bis 24 Megapixel.')
    if(!photo.dataset.ready){
      photo.width=mask.width=original.naturalWidth;photo.height=mask.height=original.naturalHeight
      photo.getContext('2d').drawImage(original,0,0);photo.dataset.ready='1'
    }
    updateFullOptions();refresh()
  }}
}
