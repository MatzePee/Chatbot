(() => {
  const hub=document.querySelector('.settings-hub')
  if(!hub)return
  const check=hub.querySelector('[data-comfy-check]')
  if(check){
    const shared=hub.querySelector('form[action="/settings/shared/comfyui"]')
    const lora=hub.querySelector('[name=comfy_character_lora]')
    const status=hub.querySelector('[data-comfy-status]')
    const inventory=hub.querySelector('[data-comfy-inventory]')
    let revision=0
    function stale(){
      revision++
      inventory.replaceChildren()
      status.textContent='Angaben geändert. Bitte erneut prüfen.'
    }
    shared.addEventListener('input',stale);lora.addEventListener('input',stale)
    const show=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n}
    async function inspect(){
      if(check.disabled || !shared.reportValidity())return
      const current=revision
      const fields=new FormData(shared)
      fields.set('comfy_character_lora',lora.value)
      check.disabled=true;status.textContent='Komponenten werden geprüft …'
      try{
        const response=await fetch('/settings/shared/comfyui/check',{method:'POST',body:fields})
        if(current!==revision)return
        if(!response.ok)throw Error('Komponentenprüfung fehlgeschlagen (HTTP '+response.status+').')
        const result=await response.json()
        if(current!==revision)return
        const available=result.rows.filter(r=>r.present===true).length
        const missing=result.rows.filter(r=>r.present===false).length
        const unknown=result.rows.length-available-missing
        status.textContent=available+' vorhanden · '+missing+' fehlen · '+unknown+' ungeprüft'
        if(unknown===result.rows.length)status.textContent+=' – ComfyUI-Adresse und Erreichbarkeit prüfen.'
        const table=document.createElement('table')
        const head=table.createTHead().insertRow()
        for(const title of ['Status','Komponente','Standardpfad / Hinweis'])head.append(show('th',title))
        const body=table.createTBody()
        for(const r of result.rows){
          const row=body.insertRow()
          row.insertCell().textContent=r.present===true?'🟢 Vorhanden':r.present===false?'🔴 Fehlt':'🟡 Ungeprüft'
          row.insertCell().textContent=r.group+' · '+r.name
          const path=row.insertCell();path.textContent=r.path;path.style.overflowWrap='anywhere'
        }
        inventory.style.overflowX='auto';inventory.replaceChildren(table)
        hub.querySelector('#comfy-loras').replaceChildren(...result.loras.map(name=>{
          const option=document.createElement('option');option.value=name;return option
        }))
      }catch(e){if(current===revision){inventory.replaceChildren();status.textContent='🟡 '+e.message}}
      finally{check.disabled=false}
    }
    check.onclick=inspect
    inspect()
  }
  let dirty=false,submitting=false
  hub.addEventListener('input',()=>{dirty=true})
  hub.addEventListener('change',()=>{dirty=true})
  const actions=new Set(['/settings','/settings/shared/ai','/settings/shared/comfyui','/settings/shared/comfyui/lora','/settings/shared/telegram','/settings/shared/updates','/settings/shared/urls','/settings/shared/fanvue'])
  hub.addEventListener('submit',async event=>{
    const form=event.target
    if(!(form instanceof HTMLFormElement)||!actions.has(new URL(form.action).pathname))return
    event.preventDefault()
    if(submitting)return
    const button=event.submitter
    if(button?.disabled)return
    submitting=true
    const data=new FormData(form)
    let status=form.querySelector('[data-save-status]')
    if(!status){status=document.createElement('p');status.dataset.saveStatus='';status.setAttribute('role','status');form.append(status)}
    status.textContent='Wird gespeichert …'
    if(button)button.disabled=true
    try {
      const response=await fetch(form.action,{method:'POST',body:data,credentials:'include'})
      if(!response.ok){
        const error=await response.json().catch(()=>({}))
        throw new Error(typeof error.detail==='string'?error.detail:'Speichern fehlgeschlagen. Bitte Eingaben prüfen.')
      }
      dirty=false
      const target=new URL(location.href);target.searchParams.set('saved','settings')
      window.CreatorStudioLive?.saved(form)
      location.assign(target.href)
    } catch(error) {
      submitting=false;status.textContent=error.message
      if(button)button.disabled=false
    }
  })
  window.addEventListener('beforeunload',event=>{
    if(!dirty||submitting)return
    if(window.CreatorStudioLive&&!window.CreatorStudioLive.dirty(hub))return
    event.preventDefault();event.returnValue=''
  })
})()
