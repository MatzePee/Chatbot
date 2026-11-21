const hub = document.querySelector('.settings-hub')
if (hub) {
  const url = new URL(location.href)
  const legacy = {
    '#ai':['shared','ai'], '#telegram':[url.searchParams.get('section') === 'autochat' || url.pathname === '/settings' ? 'autochat' : 'shared', url.searchParams.get('section') === 'autochat' || url.pathname === '/settings' ? 'notifications' : 'telegram'],
    '#fanvue':['channels','accounts'], '#updates':['shared','updates'], '#base-urls':['shared','system'], '#service-controls':['shared','system'], '#characters':['shared','characters'],
  }
  const destination = legacy[location.hash]
  if (!url.searchParams.has('view') && destination) {
    url.pathname='/settings/shared'
    url.searchParams.set('section',destination[0]);url.searchParams.set('view',destination[1])
    location.replace(url.href)
  } else if (url.searchParams.get('section') === 'personas') {
    url.searchParams.set('section','autochat');url.searchParams.set('view','profile');location.replace(url.href)
  }

  const view=hub.dataset.settingsView
  const form=hub.querySelector('[data-chat-form]')
  if(form) {
    hub.querySelectorAll('.grp,.grp-desc,[data-chat-intro]').forEach(node=>node.remove())
    const details=[...form.querySelectorAll(':scope > details.sect')]
    const groups=new Map()
    const labels={
      profile:'Chat-Stil & Tagesrhythmus', times:'Antworten & Zeiten', fans:'Fans & Prioritäten',
      sales:'Verkauf & Reaktivierung', memory:'Gedächtnis & Bilder', safety:'Freigabe & Schutzregeln', notifications:'Meldungen & Stichwort-Alarme',
    }
    const classify = summary => {
      if (['Persona / System-Prompt','Zeit & Tagesrhythmus','Anti-AI (natürlichere Antworten)'].includes(summary))return 'profile'
      if (summary.startsWith('Prio '))return 'fans'
      if (['PPV Auto-Selling','Proaktive Reaktivierung stiller Fans','Trinkgeld-Dank'].includes(summary))return 'sales'
      if (['Gesprächsgedächtnis','Bildanalyse-Verhalten','Eingehende Fan-Bilder'].includes(summary))return 'memory'
      if (['Guardrails','Freigabe-Queue: Selbstheilung'].includes(summary))return 'safety'
      if (summary==='Telegram-Benachrichtigung')return 'notifications'
      return 'times'
    }
    for(const detail of details) {
      const summary=detail.querySelector('summary')
      const key=classify(summary.textContent.trim())
      if(summary.textContent.trim()==='Anti-AI (natürlichere Antworten)')summary.textContent='Natürliches Antwortverhalten'
      if(summary.textContent.trim()==='Betrieb (allgemein)')summary.textContent='Antwortzeiten & Verarbeitung'
      if(!groups.has(key)) {
        const section=document.createElement('section');section.className='settings-subsection';section.dataset.chatGroup=key
        const heading=document.createElement('h2');heading.textContent=labels[key];section.append(heading)
        groups.set(key,section)
      }
      groups.get(key).append(detail)
    }
    for(const key of ['profile','times','fans','sales','memory','safety','notifications']) {
      const section=groups.get(key);if(!section)continue
      const visible=view==='profile'?key==='profile':view==='notifications'?key==='notifications':view==='autochat'?!['profile','notifications'].includes(key):false
      section.hidden=!visible
      form.insertBefore(section,form.querySelector(':scope > button'))
    }
    form.hidden=!['profile','notifications','autochat'].includes(view)
    hub.querySelector('[data-chat-tools]').hidden=form.hidden
    const fanvue=hub.querySelector('#fanvue')
    if(fanvue){fanvue.hidden=true;const credentials=fanvue.nextElementSibling;if(credentials?.matches('form'))credentials.hidden=true}
  }

  const aiForm=hub.querySelector('form[action="/settings/shared/ai"]')
  if(aiForm) {
    const definitions=[['access','Zugänge'],['models','Aufgabenmodelle'],['advanced','Erweiterte Generierung'],['budget','Budget']]
    const blocks=new Map()
    for(const [key,title] of definitions) {
      const block=document.createElement(key==='advanced'?'details':'section')
      block.className='settings-subsection'
      const heading=document.createElement(key==='advanced'?'summary':'h3');heading.textContent=title
      block.append(heading);blocks.set(key,block)
    }
    const advanced=['temperature','max_tokens','reasoning_mode','generation_retries','generation_retry_delay']
    for(const label of [...aiForm.querySelectorAll(':scope > label')]) {
      const name=label.querySelector('[name]')?.name || ''
      const key=name.includes('api_key')?'access':name==='openrouter_monthly_budget_usd'?'budget':advanced.includes(name)?'advanced':'models'
      blocks.get(key).append(label)
    }
    for(const note of [...aiForm.querySelectorAll(':scope > p')])blocks.get('budget').append(note)
    const save=aiForm.querySelector('button')
    for(const block of blocks.values())aiForm.insertBefore(block,save)
  }

  const styles=document.getElementById('profile-post-styles')
  if(styles) {
    async function loadStyles() {
      try {
        const {default:renderPersonas}=await import('/autoposter/js/pages/personas.js?v=persona-hashtags-20261121-1')
        styles.replaceChildren(await renderPersonas())
      } catch(error) {
        const message=document.createElement('p');message.textContent='Post-Stil konnte nicht geladen werden: '+error.message
        const retry=document.createElement('button');retry.type='button';retry.textContent='Erneut versuchen';retry.onclick=loadStyles
        styles.replaceChildren(message,retry)
      }
    }
    await loadStyles()
  }
}
