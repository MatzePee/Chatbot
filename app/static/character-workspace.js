/* One immutable URL owner per document. Never store selection in cookies/storage. */
(() => {
  'use strict';
  const initial = new URL(window.location.href);
  const owner = initial.searchParams.get('character_id');
  const nativeFetch = window.fetch.bind(window);
  const control = path => ['/api/v1/characters','/api/v1/auth','/api/v1/system',
    '/static/','/autoposter/js/','/js/','/system','/upload','/characters','/health',
    '/api/deployment-status','/api/creatorpilot/health','/api/sysinfo'].some(p => path === p || path.startsWith(p.endsWith('/') ? p : p+'/'));
  function scoped(value) {
    const url = new URL(value, window.location.href);
    if (url.origin !== initial.origin || control(url.pathname) || !owner) return url.href;
    const explicit = url.searchParams.get('character_id');
    if (explicit && explicit !== owner) throw new Error('Character context mismatch');
    url.searchParams.set('character_id',owner);
    return url.href;
  }
  window.CreatorStudioCharacters = Object.freeze({ url: scoped });
  // Capture the original owner BEFORE any async fetch/upload begins. A switch
  // creates a new document; outstanding operations cannot adopt another owner.
  if (owner) {
    window.fetch = (input,options) => {
      if (input instanceof Request) return nativeFetch(new Request(scoped(input.url),input),options);
      return nativeFetch(scoped(input),options);
    };
    document.addEventListener('click',event => {
      const link=event.target.closest && event.target.closest('a[href]');
      if (link && !link.hasAttribute('data-character-switch')) link.href=scoped(link.href);
    },true);
    document.addEventListener('submit',event => {
      const form=event.target;
      if (!(form instanceof HTMLFormElement) || form.matches('[data-character-create],[data-character-edit]')) return;
      try {
        form.action=scoped(form.action || location.href);
        // A clicked button can override form.action; hidden fields do not
        // supply the URL context required by the server middleware.
        const submitter=event.submitter;
        const overridden=submitter && submitter.hasAttribute('formaction');
        if (overridden) submitter.formAction=scoped(submitter.formAction);
        const action=overridden ? submitter.formAction : form.action;
        if (!control(new URL(action).pathname)) {
          let field=form.querySelector('input[name="character_id"]');
          if(!field){field=document.createElement('input');field.type='hidden';field.name='character_id';form.append(field);}
          field.value=owner;
        }
      } catch (error) {
        event.preventDefault();
        throw error;
      }
    },true);
  }
  function el(tag,text,attrs={}) {
    const node=document.createElement(tag);
    if(text)node.textContent=text;
    for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);
    return node;
  }
  async function init() {
    const mode=await nativeFetch('/api/v1/characters/mode',{credentials:'include'});
    if(!mode.ok)throw new Error('Charaktermodus konnte nicht geladen werden. Bitte erneut laden.');
    if(!(await mode.json()).enabled)throw new Error('Die Charakterauswahl ist auf diesem Server deaktiviert.');
    let characters=[];
    const chooser=document.querySelector('[data-character-grid]');
    document.querySelector('[data-character-manage-link]')?.addEventListener('click',event=>{if(event.currentTarget.getAttribute('aria-disabled')==='true')event.preventDefault();});
    const sheet=el('link',null,{rel:'stylesheet',href:'/static/characters.css?v=settings-structure-20261120-2'});
    document.head.append(sheet);
    function portrait(char) {
      if(char.profile_image)return el('img',null,{src:char.profile_image,alt:'',class:'character-portrait'});
      return el('span',char.name.trim().split(/\s+/).map(part=>Array.from(part)[0]).slice(0,2).join('').toUpperCase(),{class:'character-portrait character-initials','aria-hidden':'true'});
    }
    function imageField(form, char={name:'Neuer Charakter'}) {
      let value=char.profile_image || null;
      let pending=Promise.resolve();
      const field=el('div',null,{class:'character-image-field'});
      const preview=el('div',null,{class:'character-image-preview'});
      const label=el('label','Profilbild');
      const file=el('input',null,{type:'file',accept:'image/jpeg,image/png,image/webp','aria-label':'Profilbild für '+char.name});
      const remove=el('button','Bild entfernen',{type:'button'});
      const status=el('span',null,{role:'status'});
      const show=()=>{preview.replaceChildren(portrait({...char,profile_image:value}));remove.disabled=readonly||!value;};
      file.disabled=readonly;
      file.addEventListener('change',()=>{
        const selected=file.files[0];if(!selected)return;
        pending=(async()=>{
          try{
            if(selected.size>15*1024*1024)throw new Error('Bitte ein Bild bis 15 MB auswählen.');
            const bitmap=await createImageBitmap(selected);
            const canvas=document.createElement('canvas');
            const scale=Math.min(1,512/Math.max(bitmap.width,bitmap.height));
            canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
            canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
            value=canvas.toDataURL('image/webp',0.85);show();status.textContent='Bild ausgewählt. Mit Speichern übernehmen.';
          }catch(error){status.textContent=error.message||'Bild konnte nicht gelesen werden.';}
        })();
      });
      remove.addEventListener('click',()=>{value=null;file.value='';show();status.textContent='Entfernen mit Speichern übernehmen.';});
      label.append(file);field.append(preview,label,remove,status);form.append(field);show();
      return {async get(){await pending;return value;},reset(){value=null;file.value='';status.textContent='';show();}};
    }
    const style=el('style');
    style.textContent=`.character-brand-stack{display:flex;flex-direction:column;gap:4px;flex:none;min-width:0;max-width:100%}.workspace-topbar.has-character-selector{height:auto;min-height:78px}.character-workspace{box-sizing:border-box;display:block;flex:none;max-width:220px;padding:0;font:12px system-ui}.character-workspace select{box-sizing:border-box;width:100%;max-width:220px;min-width:100px;height:29px;padding:3px 25px 3px 8px;margin:0;border:1px solid #cdd4df;border-radius:7px;background:#fff;color:#253047;font:inherit}.character-workspace a{display:block;margin-top:8px}.character-edit-row{display:flex;flex-wrap:wrap;align-items:end;gap:8px;margin:12px 0}.character-edit-row label{flex:1 1 180px;min-width:0;margin:0}.character-edit-row input{box-sizing:border-box;width:100%;max-width:100%}.character-edit-row [role=status]{flex-basis:100%;overflow-wrap:anywhere}[data-character-management] [hidden]{display:none!important}`;
    document.head.append(style);
    const bar=el('div',null,{class:'character-workspace','data-live-preserve':''});
    const select=el('select',null,{id:'character-selector','data-character-selector':'','aria-label':'Charakter auswählen'});
    const characterLink=el('a',null,{class:'character-switch-link',href:'/characters','data-character-switch':'','aria-label':'Charakter wechseln'});
    if(!chooser){
      const returnTo=new URL(location.href);
      returnTo.searchParams.delete('character_id');
      characterLink.href='/characters?return_to='+encodeURIComponent(returnTo.pathname+returnTo.search+returnTo.hash);
      bar.append(characterLink);
    }
    function switchTo(id){const url=new URL(location.href);url.searchParams.set('character_id',id);if(url.pathname==='/characters')url.pathname='/autoposter/';location.assign(url.href);}
    select.addEventListener('change',()=>{if(select.value)switchTo(select.value);});
    function placeSelector(){
      if(chooser)return;
      const brand=document.querySelector('.workspace-topbar .workspace-brand');
      if(brand){
        let stack=brand.closest('.character-brand-stack');
        if(!stack){stack=el('div',null,{class:'character-brand-stack'});brand.before(stack);stack.append(brand);}
        if(bar.parentNode!==stack)stack.append(bar);
        const header=stack.closest('.workspace-topbar');
        if(!header.classList.contains('has-character-selector'))header.classList.add('has-character-selector');
      }else if(!bar.isConnected){document.querySelector('main')?.prepend(bar);if(!bar.isConnected)document.body.prepend(bar);}
    }
    const panel=document.querySelector('[data-character-management]');
    const readonly=panel?.dataset.readonly==='true';
    const rows=new Map();
    async function request(method,path,body){
      const response=await nativeFetch(path,{method,credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Änderung nicht möglich');
      return result;
    }
    function render(){
      select.replaceChildren();
      if(!owner)select.append(el('option','Bitte auswählen',{value:''}));
      for(const char of characters)select.append(el('option',char.name,{value:char.id}));
      if(owner)select.value=owner;
      const current=characters.find(char=>char.id===owner);
      characterLink.replaceChildren();
      if(current){
        characterLink.append(portrait(current),el('span',current.name),el('span','⌄',{'aria-hidden':'true'}));
        const scope=document.querySelector('[data-settings-scope]');
        if(scope?.textContent.trim().startsWith('Für '))scope.textContent='Für '+current.name;
        const group=document.querySelector('[data-settings-nav] .workspace-nav-heading');
        if(group)group.textContent='Für '+current.name;
      }
      else characterLink.append(el('span','Charakter auswählen'));
      if(chooser){
        chooser.replaceChildren();
        for(const char of characters){
          let target=new URL('/autoposter/',location.origin);
          const returnTo=initial.searchParams.get('return_to');
          if(returnTo){
            const candidate=new URL(returnTo,location.origin);
            if(candidate.origin===location.origin && !candidate.pathname.startsWith('/characters'))target=candidate;
          }
          target.searchParams.set('character_id',char.id);
          const tile=el('a',null,{class:'character-tile',href:target.href,'data-character-switch':''});
          tile.append(portrait(char),el('strong',char.name),el('span','Arbeitsbereich öffnen →',{class:'character-tile-action'}));chooser.append(tile);
        }
        if(!characters.length)chooser.append(el('p','Noch keine Charaktere vorhanden. Lege deinen ersten Charakter in den Einstellungen an.'));
        const manage=document.querySelector('[data-character-manage-link]');
        const id=owner||characters[0]?.id;
        if(id){
          manage.href='/settings/shared?section=shared&view=characters&character_id='+encodeURIComponent(id)+'#characters';
          manage.removeAttribute('aria-disabled');manage.removeAttribute('tabindex');
        }
        document.querySelector('[data-character-load-status]').textContent='';
      }
      if(!panel)return;
      panel.hidden=false;
      for(const char of characters){
        if(panel.dataset.currentOnly==='true' && char.id!==owner)continue;
        let form=rows.get(char.id);
        if(!form){
          form=el('form',null,{'data-character-edit':char.id,class:'character-edit-row'});
          const label=el('label','Name');
          const input=el('input',null,{name:'name',required:'',maxlength:'120','aria-label':'Name von '+char.name});
          const save=el('button','Speichern',{type:'submit',class:'btn-primary'});
          const status=el('span',null,{role:'status'});
          label.append(input);form.append(label,save,status);rows.set(char.id,form);panel.querySelector('[data-character-list]').append(form);
          const profile=imageField(form,char);
          input.value=char.name;form.dataset.storedName=char.name;
          input.disabled=save.disabled=readonly;
          form.addEventListener('submit',async event=>{
            event.preventDefault();event.stopPropagation();if(readonly)return;
            const name=input.value.trim();if(!name){status.textContent='Name erforderlich';return;}
            save.disabled=true;status.textContent='Wird gespeichert …';
            try{
              const result=await request('PATCH','/api/v1/characters/'+encodeURIComponent(char.id),{name,profile_image:await profile.get()});
              await refresh();
              if(!characters.some(c=>c.id===result.id&&c.name===result.name))throw new Error('Änderung noch nicht bestätigt. Bitte neu laden.');
              input.value=result.name;form.dataset.storedName=result.name;status.textContent='Gespeichert.';globalThis.CreatorStudioLive?.saved?.(form);
            }catch(error){status.textContent=error.message;}finally{save.disabled=readonly;}
          });
        }else{
          const input=form.elements.namedItem('name');
          if(input.value===form.dataset.storedName){input.value=char.name;form.dataset.storedName=char.name;}
        }
      }
    }
    async function refresh(){
      const response=await nativeFetch('/api/v1/characters',{credentials:'include'});
      if(!response.ok)throw new Error('Charaktere konnten nicht geladen werden.');
      characters=await response.json();render();
    }
    await refresh();placeSelector();
    new MutationObserver(placeSelector).observe(document.body,{childList:true,subtree:true});
    if(panel){
      const form=panel.querySelector('[data-character-create]'),input=form.elements.namedItem('name'),save=form.querySelector('button'),status=panel.querySelector('[data-character-create-status]');
      form.hidden=panel.dataset.currentOnly==='true';
      const profile=imageField(form);
      input.disabled=save.disabled=readonly;
      form.addEventListener('submit',async event=>{
        event.preventDefault();event.stopPropagation();if(readonly)return;
        const name=input.value.trim();if(!name){status.textContent='Name erforderlich';return;}
        save.disabled=true;status.textContent='Wird erstellt …';
        try{
          const result=await request('POST','/api/v1/characters',{name,profile_image:await profile.get()});await refresh();
          if(!characters.some(c=>c.id===result.id&&c.name===result.name))throw new Error('Anlage noch nicht bestätigt. Bitte vor einem erneuten Versuch neu laden.');
          profile.reset();input.value='';status.textContent='Charakter leer und deaktiviert angelegt.';globalThis.CreatorStudioLive?.saved?.(form);
        }catch(error){status.textContent=error.message;}finally{save.disabled=readonly;}
      });
    }else if(location.pathname==='/characters'&&!chooser){
      const id=owner||characters[0]?.id;
      const target=new URL('/settings/shared',location.origin);if(id)target.searchParams.set('character_id',id);target.hash='characters';
      bar.append(el('a','Charaktere in Einstellungen → Gemeinsam verwalten',{href:target.href,'data-character-switch':''}));
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>init().catch(error=>{console.error(error);const status=document.querySelector('[data-character-load-status]');if(status)status.textContent=error.message;}));
  else init().catch(error=>{console.error(error);const status=document.querySelector('[data-character-load-status]');if(status)status.textContent=error.message;});
})();
