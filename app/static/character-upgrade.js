(() => {
 const card=document.getElementById('character-upgrade');if(!card)return;
 const form=card.querySelector('form'),status=card.querySelector('[role=status]');
 const name=form.elements.name,confirmed=form.elements.confirmed,submit=form.querySelector('[type=submit]'),cancel=card.querySelector('#character-upgrade-cancel');
 let phase='loading',busy=false,helperReady=false;
 function controls(){submit.disabled=busy||!helperReady||!['legacy','failed'].includes(phase)||!confirmed.checked||!name.value.trim();cancel.hidden=phase!=='pending_restart';}
 function render(data){phase=data.phase;helperReady=data.helper?.ready===true;
  status.textContent=phase!=='active'&&!helperReady?(data.helper?.message||'Administrator erforderlich: Installierter Helfer oder Dienstkonfiguration konnte nicht geprüft werden. Keine Umstellung vormerken.'):phase==='active'?'Charakterbetrieb aktiv: '+data.name:phase==='pending_restart'?'Vorgemerkt: '+data.name+' – jetzt den vorhandenen Dienstneustart verwenden.':phase==='failed'?'Umstellung verweigert. Originalinstallation erhalten. Gestoppte Worker, Schema, Pfade und technische Einstellungskonflikte prüfen.':'Einzelcharakter bleibt unverändert';
  if(data.name)name.value=data.name;if(data.technical_sources&&form.elements.technical_source){const values=[...new Set(Object.values(data.technical_sources))];form.elements.technical_source.value=values.length===1?values[0]:'';}controls();
 }
 async function request(method='GET',body){busy=true;controls();try{
  const result=await fetch('/api/v1/system/character-upgrade',{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  if(!result.ok){const error=await result.json();if(result.status===409){render({phase,helper:{ready:false,message:typeof error.detail==='string'?error.detail:'Administrator erforderlich: Umstellung gesperrt.'}});return;}throw Error();}render(await result.json());
 }catch{status.textContent='Umstellungsstatus nicht verfügbar. Es wurde kein Dienstneustart ausgelöst.';phase='error';}
 finally{busy=false;controls();}}
 form.addEventListener('input',controls);
 form.addEventListener('submit',event=>{event.preventDefault();if(!submit.disabled){const body={name:name.value.trim(),confirmed:confirmed.checked};const source=form.elements.technical_source?.value;if(source)body.technical_source=source;request('POST',body);}});
 cancel.addEventListener('click',()=>request('DELETE'));
 request();
})();
