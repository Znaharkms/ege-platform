"use strict";
window.addEventListener('DOMContentLoaded',()=>{
  const inProfile=location.pathname.replace(/\/$/,'')==='/profile';
  let generation=0;
  async function init(){
    const run=++generation;document.getElementById('student-inbox')?.remove();document.getElementById('inbox-dialog')?.remove();
    const user=await window.accountIdentity();if(run!==generation||!user||user.role==='admin')return;
    const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
    function publish(value){window.studentUnreadCount=value;document.querySelectorAll('.account-messages-count').forEach(b=>{b.textContent=String(value);b.hidden=!value;});window.dispatchEvent(new CustomEvent('unread-count-changed',{detail:value}));}
    if(inProfile){
      async function refresh(){try{const data=await accountJson('/history-trainer/error-reports/mine?markViewed=false');if(run===generation)publish(data.unreadCount||0);}catch{}}
      await refresh();const timer=setInterval(()=>{if(run!==generation){clearInterval(timer);return;}refresh();},30000);
      window.addEventListener('messages-changed',()=>{if(run===generation)refresh();});return;
    }
    const wrap=el('div',null,'student-inbox');wrap.id='student-inbox';const bell=el('button',null,'inbox-bell');bell.type='button';bell.setAttribute('aria-label','Входящие сообщения');bell.setAttribute('aria-haspopup','dialog');
    bell.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>';
    const badge=el('span','0','inbox-count');badge.hidden=true;badge.setAttribute('aria-hidden','true');bell.append(badge);wrap.append(bell);(document.querySelector('header nav')||document.querySelector('header')).append(wrap);
    const dialog=el('dialog',null,'inbox-dialog');dialog.id='inbox-dialog';dialog.setAttribute('aria-labelledby','inbox-title');const heading=el('h2','Входящие сообщения');heading.id='inbox-title';const close=el('button','×','inbox-close');close.type='button';close.setAttribute('aria-label','Закрыть входящие сообщения');close.onclick=()=>dialog.close();
    const status=el('p');status.setAttribute('role','status');const list=el('div',null,'inbox-list'),detail=el('section',null,'inbox-detail');dialog.append(close,heading,status,list,detail);document.body.append(dialog);let current=null;
    function count(value){publish(value);badge.textContent=String(value);badge.hidden=!value;bell.setAttribute('aria-label',value?`Входящие сообщения, непрочитанных: ${value}`:'Входящие сообщения, новых нет');}
    async function load(){try{const data=await accountJson('/history-trainer/error-reports/mine?markViewed=false');if(run!==generation||!wrap.isConnected)return;count(data.unreadCount||0);if(!dialog.open)return;list.replaceChildren();const incoming=data.items.filter(x=>x.adminReply);status.textContent=incoming.length?'':'Входящих сообщений пока нет.';
      incoming.forEach(item=>{const b=el('button',null,`inbox-message${item.unread?' unread':''}`);b.type='button';b.append(el('strong',item.unread?'Новый ответ администратора':'Ответ администратора'),el('small',new Date(item.repliedAt||item.createdAt).toLocaleString('ru-RU')),el('span',item.adminReply));b.onclick=async()=>{
        current=item.id;detail.replaceChildren(el('h3','Ответ администратора'),el('p',item.adminReply),el('h4','Твоё обращение'),el('p',item.message));if(item.questionPreview)detail.append(el('p',item.questionPreview));detail.scrollIntoView({block:'nearest'});
        if(item.unread){try{await accountJson(`/me/messages/${encodeURIComponent(item.id)}/read`,{method:'POST'});if(current===item.id)await load();}catch(e){status.textContent='Ответ открыт, но не удалось отметить его прочитанным. Попробуй ещё раз.';}}
      };list.append(b);});
    }catch(e){if(dialog.open)status.textContent=e.message;}}
    window.addEventListener('messages-changed',()=>{if(run===generation)load();});
    bell.onclick=()=>{detail.replaceChildren();status.textContent='Загружаем сообщения…';dialog.showModal();load();};dialog.addEventListener('close',()=>bell.focus());await load();
    const timer=setInterval(()=>{if(run!==generation||!wrap.isConnected){clearInterval(timer);return;}load();},30000);
    document.addEventListener('visibilitychange',()=>{if(run===generation && !document.hidden)load();});
  }
  window.addEventListener('account-changed',init);init();
});
