"use strict";
// Access tokens stay in memory; the HttpOnly refresh cookie restores the session.
let accessToken=null, student=null, restoring=null;
async function restoreAccount(){
  if(!restoring)restoring=(async()=>{
    const response=await fetch('/api/v1/auth/refresh',{method:'POST'});
    if(response.ok){accessToken=(await response.json()).accessToken;const me=await fetch('/api/v1/me',{headers:{Authorization:`Bearer ${accessToken}`}});if(me.ok)student=await me.json();}
  })().catch(()=>{});
  await restoring;
}
window.accountIdentity=async()=>{await restoreAccount();return student;};
window.acceptAccount=function(data){
  accessToken=data.accessToken;student=data.user;restoring=Promise.resolve();
  window.dispatchEvent(new Event('account-changed'));
};
window.clearAccount=function(){
  accessToken=null;student=null;restoring=Promise.resolve();
  sessionStorage.removeItem('egeAdminToken');
  window.dispatchEvent(new Event('account-changed'));
};
window.accountRequest=async function(endpoint,options={}){
  await restoreAccount();
  const send=()=>{const headers=new Headers(options.headers);if(accessToken)headers.set('Authorization',`Bearer ${accessToken}`);return fetch(`/api/v1${endpoint}`,{...options,headers});};
  let response=await send();
  if(response.status===401 && accessToken){accessToken=null;student=null;restoring=null;await restoreAccount();response=await send();}
  return response;
};
async function accountJson(endpoint,options={}){
  const response=await window.accountRequest(endpoint,options);
  if(!response.ok){
    let problem={};try{problem=await response.json();}catch{}
    const message=problem.code==='smtp_authentication_failed' || problem.code==='email_delivery_unavailable' ? problem.detail || 'Отправка писем пока не настроена.' : response.status===429 ? 'Слишком много попыток. Попробуй позднее.' : response.status===503 ? 'Отправка писем пока не настроена.' : endpoint.includes('verify-code') ? 'Код неверный или истёк. Запроси новый код.' : 'Не удалось выполнить действие. Попробуй ещё раз.';
    const error=new Error(problem.detail || problem.title || message);error.code=problem.code;throw error;
  }
  return response.status===204 ? null : response.json();
}
window.showFavorite=async function(id,b=document.getElementById('favorite-material'),status=document.getElementById('favorite-status')){
  b.dataset.id=id;b.hidden=false;b.disabled=true;
  await restoreAccount();if(b.dataset.id!==id)return;
  let saved=false;
  if(student){try{const r=await accountJson(`/content/${encodeURIComponent(id)}`);if(b.dataset.id!==id)return;saved=Boolean(r.isFavorite);}catch{status.textContent='Не удалось проверить избранное.';}}
  const label=()=>{b.textContent=saved ? '♥' : '♡';b.setAttribute('aria-label',saved?'Убрать из избранного':'Добавить в избранное');b.setAttribute('aria-pressed',String(saved));b.title=saved?'Убрать из избранного':'Добавить в избранное';};label();b.disabled=false;
  b.onclick=async()=>{
    if(!student){location.assign(`/profile?material=${encodeURIComponent(id)}`);return;}
    b.disabled=true;status.textContent='';
    try{await accountJson(`/me/favorites/${encodeURIComponent(id)}`,{method:saved ? 'DELETE' : 'PUT'});saved=!saved;label();status.textContent=saved ? 'Материал сохранён в кабинете.' : 'Материал удалён из избранного.';}catch(e){status.textContent=e.message;}finally{b.disabled=false;}
  };
};
window.addEventListener('DOMContentLoaded',async()=>{
  if(location.pathname.replace(/\/$/,'')!=='/profile')return;
  document.body.classList.add('profile-page');document.title='Личный кабинет — Олеся Блок';
  for(const selector of ['.search-heading','#search-subject-label','#search-form','#directories','#plan-search','#catalog'])document.querySelector(selector).hidden=true;
  document.getElementById('page-title').textContent='Личный кабинет';document.getElementById('intro').textContent='Сохраняй нужные даты, термины и планы для повторения.';
  const art=document.createElement('img');art.className='cabinet-art';art.src='/learn/assets/plans-art.png';art.alt='';art.setAttribute('aria-hidden','true');document.getElementById('hero').append(art);
  const root=document.createElement('section');root.className='account-panel';document.querySelector('main').append(root);
  const el=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  root.textContent='Загружаем кабинет…';await restoreAccount();
  async function render(){
    root.replaceChildren();root.className="account-panel";
    if(!student){
      root.append(el('h2','Вход и регистрация'),el('p','Введи почту и получи код. При первом входе аккаунт создаётся автоматически.'));
      const form=el('form'),email=el('input'),name=el('input'),code=el('input'),submit=el('button','Получить код'),notice=el('p');notice.setAttribute('role','status');
      email.type='email';email.required=true;email.autocomplete='email';email.maxLength=254;name.autocomplete='name';name.maxLength=120;code.inputMode='numeric';code.pattern='[0-9]{6}';code.maxLength=6;code.autocomplete='one-time-code';
      const field=(text,input)=>{const l=el('label',text);l.append(input);form.append(l);return l;};field('Электронная почта',email);const nameLabel=field('Как вас зовут или ваш ник',name);nameLabel.hidden=true;const codeLabel=field('Код из письма',code);codeLabel.hidden=true;submit.type='submit';form.append(submit,notice);root.append(form);
      let verifying=false;const again=el('button','Запросить новый код');again.type='button';again.hidden=true;form.append(again);
      async function requestCode(){const delivery=await accountJson('/auth/email/request-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email.value.trim()})});verifying=true;email.readOnly=true;codeLabel.hidden=false;code.required=true;submit.textContent='Войти';again.hidden=false;notice.textContent=delivery.deliveryMode==='development' ? 'Отправка писем пока не настроена. Код для тестового входа есть у владельца платформы.' : `Код отправлен. Он действует ${Math.ceil((delivery.expiresIn || 600)/60)} минут.`;code.focus();}
      again.onclick=async()=>{again.disabled=true;try{await requestCode();}catch(e){notice.textContent=e.message;}finally{again.disabled=false;}};
      form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;notice.textContent='';try{
        if(!verifying)await requestCode();else{const data=await accountJson('/auth/email/verify-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email.value.trim(),code:code.value.trim(),...(name.value.trim()?{displayName:name.value.trim()}:{})})});window.acceptAccount(data);await render();}
      }catch(e){notice.textContent=e.message;if(e.code==='registration_name_required'){nameLabel.hidden=false;name.required=true;submit.textContent='Зарегистрироваться';name.focus();}else if(verifying){const alert=el('div',e.message);alert.className='login-code-alert';alert.setAttribute('role','alert');document.body.append(alert);setTimeout(()=>alert.remove(),6000);code.setAttribute('aria-invalid','true');code.focus();}}finally{submit.disabled=false;}};
      const change=el('button','Указать другую почту');change.type='button';change.onclick=()=>render();form.append(change);return;
    }
    root.className='account-shell';
    const sidebar=el('aside'),nav=el('nav'),content=el('div');sidebar.className='account-sidebar';content.className='account-content account-panel';nav.setAttribute('aria-label','Разделы личного кабинета');root.append(sidebar,content);
    sidebar.append(el('strong',student.displayName || 'Мой кабинет'));
    const sections=new Map(),buttons=new Map();
    const labels=[['profile','Мой профиль'],['favorites','Избранное'],['practice','Моя подготовка'],['messages',student.role==='admin'?'Обращения учеников':'Сообщения'],['searches','История поиска']];
    let selected=labels.some(([key])=>key===location.hash.slice(1))?location.hash.slice(1):'profile';
    function select(key,updateUrl=true){selected=key;sections.forEach((section,name)=>section.hidden=name!==key);buttons.forEach((button,name)=>{button.classList.toggle('active',name===key);if(name===key)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});if(updateUrl){const url=new URL(location.href);url.hash=key;history.replaceState(null,'',url);}}
    function mount(key,section){sections.get(key)?.remove();section.dataset.accountSection=key;section.hidden=key!==selected;sections.set(key,section);content.append(section);}
    labels.forEach(([key,title])=>{const b=el('button');const icon=el('span');icon.className='account-nav-icon';icon.setAttribute('aria-hidden','true');
      const paths={profile:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4 21v-2a8 8 0 0 1 16 0v2',favorites:'m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z',practice:'M4 20h16 M6 16v-5 M12 16V7 M18 16V3',messages:'M3 5h18v14H3z M3 5l9 7 9-7',searches:'M4 12a8 8 0 1 0 2-5 M3 3v5h5 M12 7v5l3 2'};
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');const line=document.createElementNS(svg.namespaceURI,'path');line.setAttribute('d',paths[key]);svg.append(line);icon.append(svg);b.append(icon,el('span',title));if(key==='messages' && student.role!=='admin'){const badge=el('span',String(window.studentUnreadCount||0));badge.className='account-messages-count';badge.hidden=!window.studentUnreadCount;badge.setAttribute('aria-label','Непрочитанные сообщения');b.append(badge);}b.type='button';b.onclick=()=>select(key);buttons.set(key,b);nav.append(b);});sidebar.append(nav);select(selected,false);
    const profileSection=el('section');mount('profile',profileSection);profileSection.append(el('h2','Мой профиль'),el('p',student.email));
    const favoriteSection=el('section');mount('favorites',favoriteSection);
    labels.slice(2).forEach(([key,title])=>{const section=el('section');section.append(el('h2',title),el('p','Загружаем раздел…'));mount(key,section);});

    const logout=el('button','Выйти');logout.type='button';sidebar.append(logout);logout.onclick=async()=>{logout.disabled=true;try{await accountJson('/auth/logout',{method:'POST'});window.clearAccount();await render();}catch(e){profileSection.append(el('p',e.message));logout.disabled=false;}};
    const profile=el('form'),name=el('input');name.value=student.displayName || '';name.maxLength=120;const label=el('label','Имя');label.append(name);const save=el('button','Сохранить имя'),notice=el('p');notice.setAttribute('role','status');profile.append(label,save,notice);profileSection.append(profile);if(student.role!=='admin'&&window.mountPromotion)window.mountPromotion(profileSection,'cabinet');profile.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{student=await accountJson('/me',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({displayName:name.value.trim()})});notice.textContent='Имя сохранено.';sidebar.querySelector('strong').textContent=student.displayName || 'Мой кабинет';}catch(err){notice.textContent=err.message;}finally{save.disabled=false;}};
    favoriteSection.append(el('h2','Избранное'));const groups=new Map();
    [['history-term','Термины по истории'],['history-date','Даты'],['society-term','Термины по обществознанию'],['society-plan','Планы']].forEach(([key,title])=>{const group=el('details'),heading=el('summary',title),list=el('div');group.className='favorite-group';list.className='cards';group.append(heading,list);favoriteSection.append(group);groups.set(key,{group,heading,list,title,count:0});});
    const more=el('button','Показать ещё'),message=el('p');message.setAttribute('role','status');favoriteSection.append(message,more);let cursor=null,total=0;const seen=new Set();
    async function load(){more.disabled=true;try{const data=await accountJson(`/me/favorites?${new URLSearchParams({limit:'100',...(cursor?{cursor}:{})})}`);data.items.forEach(item=>{if(seen.has(item.id))return;seen.add(item.id);const g=groups.get(`${item.subject}-${item.type}`);if(g){g.list.append(card(item));g.count++;total++;g.heading.textContent=`${g.title} · ${g.count}`;}});cursor=data.page?.nextCursor;more.hidden=!data.page?.hasMore;message.textContent=total?'':'Пока ничего не сохранено. Нажми сердечко у материала.';}catch(e){message.textContent=e.message;more.textContent='Повторить';}finally{more.disabled=false;}}
    more.onclick=load;await load();
    document.getElementById('material').onclose=()=>{groups.forEach(g=>{g.list.replaceChildren();g.count=0;g.heading.textContent=g.title;});seen.clear();total=0;cursor=null;load();};
    if(window.renderStudentDashboard)await window.renderStudentDashboard(content,mount);
    const material=new URLSearchParams(location.search).get('material');if(material){history.replaceState(null,'','/profile');openMaterial(material);}
  }
  await render();
});

// Record visible user interactions, rather than notification polling.
let lastActivityPing=0;
function recordAccountActivity(){
  if(!student || document.visibilityState!=='visible' || Date.now()-lastActivityPing<60000)return;
  lastActivityPing=Date.now();
  window.accountRequest('/me/activity',{method:'POST'}).catch(()=>{});
}
window.addEventListener('pointerdown',recordAccountActivity,{passive:true});
window.addEventListener('keydown',recordAccountActivity);
