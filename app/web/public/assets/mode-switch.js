"use strict";
// A view switch only: permissions remain governed by the authenticated API role.
window.addEventListener('DOMContentLoaded',()=>{
  let revision=0;
  async function update(){
    const run=++revision;
    document.getElementById('admin-mode-switch')?.remove();
    const user=await window.accountIdentity();
    if(run!==revision || user?.role!=='admin')return;
    const isAdmin=/^\/admin(?:\/|$)/.test(location.pathname);
    if(!isAdmin){try{sessionStorage.setItem('egeLastStudentPage',location.pathname+location.search);}catch{}}
    const nav=document.createElement('nav');nav.id='admin-mode-switch';nav.className='mode-switch';nav.setAttribute('aria-label','Режим просмотра платформы');
    const label=document.createElement('span');label.className='mode-switch-label';label.textContent='Режим просмотра';nav.append(label);
    let studentPath='/';try{const stored=sessionStorage.getItem('egeLastStudentPage');if(stored && /^\/(?:$|history(?:[/?]|$)|society(?:[/?]|$)|search(?:[/?]|$)|profile(?:[/?]|$)|trainer(?:[/?]|$))/.test(stored))studentPath=stored;}catch{}
    [['Пользователь',studentPath,!isAdmin],['Администратор','/admin',isAdmin]].forEach(([title,url,active])=>{
      const item=document.createElement(active?'span':'a');item.textContent=title;
      if(active){item.className='current-mode';item.setAttribute('aria-current','page');}else item.href=url;
      nav.append(item);
    });
    const sidebar=document.querySelector('.sidebar-brand');
    if(isAdmin && sidebar)sidebar.after(nav);else document.querySelector('header')?.after(nav);
  }
  window.addEventListener('account-changed',update);update();
});
