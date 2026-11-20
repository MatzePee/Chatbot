(() => {
  const hub=document.querySelector('.settings-hub')
  if(!hub)return
  let dirty=false,submitting=false
  hub.addEventListener('input',()=>{dirty=true})
  hub.addEventListener('change',()=>{dirty=true})
  const actions=new Set(['/settings','/settings/shared/ai','/settings/shared/telegram','/settings/shared/updates','/settings/shared/urls','/settings/shared/fanvue'])
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
