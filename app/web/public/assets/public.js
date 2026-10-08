"use strict";
const $ = (id) => document.getElementById(id);
const path = location.pathname.replace(/\/$/, "") || "/";
const routes = {
  "/history/dates": {subject:"history",type:"date",title:"Даты по истории",intro:"Повторяй события и находи нужные даты по периодам."},
  "/history/terms": {subject:"history",type:"term",title:"Термины по истории",intro:"Исторические понятия и их определения."},
  "/society/terms": {subject:"society",type:"term",title:"Термины по обществознанию",intro:"Находи определения и повторяй понятия по разделам."},
  "/society/plans": {subject:"society",type:"plan",title:"Планы по обществознанию",intro:"Открывай тему и повторяй пункты развёрнутого плана."}
};
const route = routes[path] || {};
$('search-form').classList.add('reference-search-field');
$('plan-search-form').classList.add('reference-search-field');
const urlType=new URLSearchParams(location.search).get('type');
const searchType=route.type || (path==='/search' && ['date','term','plan'].includes(urlType) ? urlType : null);
if(path === "/"){
  document.body.classList.add("subject-home");
  for(const selector of [".search-heading","#search-subject-label","#search-form","#global-search-status"])document.querySelector(selector).hidden=true;
}
if(["/history","/society"].includes(path))document.body.classList.add("subject-landing");

const urlSubject=new URLSearchParams(location.search).get("subject");
const subject = route.subject || (path === "/history" ? "history" : path === "/society" ? "society" : path === "/search" && ["history","society"].includes(urlSubject) ? urlSubject : null);
if(subject)document.body.classList.add(`${subject}-page`);
if(route.type)document.body.classList.add(`reference-${route.type}`);
const pageArt={"/history":"history-terms-art","/society":"society-terms-art","/history/dates":"dates-art","/history/terms":"history-terms-art","/society/terms":"society-terms-art","/society/plans":"plans-art"};
if(pageArt[path]){
  document.body.classList.add("illustrated-reference");
  const art=node("img",null,"reference-art");art.src=`/learn/assets/${pageArt[path]}.png`;art.alt="";art.setAttribute("aria-hidden","true");$("hero").append(art);
}
const types = {date:"Дата",term:"Термин",plan:"План"};
let cursor = null, sequence = 0, detailSequence = 0, query = new URLSearchParams(location.search).get("q") || "";
let controller, loaded = 0, lastFocus;
function node(tag, text, className) { const n=document.createElement(tag); if(text != null)n.textContent=text; if(className)n.className=className; return n; }
async function api(endpoint, signal) {
  const response=await window.accountRequest(endpoint, {signal});
  if(!response.ok) throw new Error(response.status === 404 ? "Материал недоступен." : "Не удалось загрузить материалы. Попробуй ещё раз.");
  return response.json();
}
function toolIcon(label){
  const paths={
    "Даты":"M5 5h14v15H5z M8 2v6 M16 2v6 M5 10h14 M8 14h2 M14 14h2 M8 17h2",
    "Термины":"M12 5C8 2 3 3 3 3v16s5-1 9 2c4-3 9-2 9-2V3s-5-1-9 2z M12 5v16",
    "Планы":"M6 3h12v18H6z M9 8h6 M9 12h6 M9 16h4",
    "Тренажёр":"M20 12a8 8 0 1 1-8-8 M16 3v5h5 M12 12l8-8 M15 12a3 3 0 1 1-3-3"
  };
  const span=node("span",null,"tool-icon");
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("aria-hidden","true");
  const line=document.createElementNS("http://www.w3.org/2000/svg","path");line.setAttribute("d",paths[label]);line.setAttribute("fill","none");line.setAttribute("stroke","currentColor");line.setAttribute("stroke-width","1.6");line.setAttribute("stroke-linejoin","round");line.setAttribute("stroke-linecap","round");svg.append(line);span.append(svg);return span;
}
function directory(title, description, links) {
  const historySubject=title === "История";
  const card=node("article",null,`directory ${historySubject ? "history-directory" : "society-directory"}`);
  card.append(node("p",historySubject ? "ДАТЫ · СОБЫТИЯ · ПОНЯТИЯ" : "ЧЕЛОВЕК · ОБЩЕСТВО · ПРАВО","subject-kicker"),node("h2",title),node("p",description,"subject-description"));
  const row=node("div",null,"links");
  const descriptions={"Даты":"События и хронология по периодам","Термины":"Понятия и точные определения","Планы":"Пункты и подпункты по темам","Тренажёр":"Проверяй знания на заданиях ЕГЭ"};
  links.forEach(([label,url])=>{
    const a=node("a",null,label === "Тренажёр" ? "subject-link practice-link" : "subject-link");a.href=url;
    a.append(toolIcon(label),node("strong",label),node("span",descriptions[label],"tool-description"),node("span","→","tool-arrow"));row.append(a);
  });if(!historySubject){const soon=node("div",null,"subject-link coming-soon");soon.setAttribute("aria-disabled","true");soon.append(toolIcon("Тренажёр"),node("strong","Тренажёр"),node("span","Скоро","tool-description"));row.append(soon);}
  card.append(row);$("directories").append(card);
}
if(!subject || subject === "history")directory("История","Ключевые даты, термины и тренировка знаний для подготовки к ЕГЭ.",[["Даты","/history/dates"],["Термины","/history/terms"],["Тренажёр","/trainer/history"]]);
if(!subject || subject === "society")directory("Обществознание","Термины и структурированные планы по всем разделам предмета.",[["Термины","/society/terms"],["Планы","/society/plans"]]);
if(route.title){$("page-title").textContent=route.title;$("intro").textContent=route.intro;$("directories").hidden=true;}
else if(subject){$("page-title").textContent=subject === "history" ? "История" : "Обществознание";$("intro").textContent="Выбери справочник или найди материал по теме.";}
else if(path === "/search"){$("page-title").textContent="Поиск по материалам";$("intro").textContent="Даты, термины и планы в одном поиске.";$("directories").hidden=true;}
if(document.body.classList.contains("subject-landing")){
  document.querySelector(".search-heading strong").textContent=subject === "history" ? "Поиск по истории" : "Поиск по обществознанию";
  document.querySelector(".search-heading div>span").textContent=subject === "history" ? "Даты и исторические термины" : "Термины и темы планов";
  $("query").placeholder=subject === "history" ? "Найти дату, событие или термин…" : "Найти термин или тему плана…";
}
if(subject){
  document.body.classList.add('subject-workspace');
  document.querySelector('.search-heading').hidden=true;
  const menu=node('nav',null,'subject-subnav');menu.setAttribute('aria-label','Разделы предмета');
  const links=subject==='history' ? [['Даты','/history/dates'],['Термины','/history/terms'],['Тренажёр','/trainer/history']] : [['Термины','/society/terms'],['Планы','/society/plans']];
  links.forEach(([title,url])=>{const a=node('a',null);a.href=url;a.append(toolIcon(title),node('span',title));if(path===url)a.setAttribute('aria-current','page');menu.append(a);});
  document.querySelector('.search-heading').before(menu);
  $('directories').hidden=true;
  const toolbar=document.querySelector('.toolbar');
  (path==='/society/plans' ? $('plan-search') : $('global-search-status')).after(toolbar);
}
if(subject && path!=='/search'){
  document.body.classList.add('compact-subject-entrance');
  const art=document.querySelector('.reference-art');
  if(art){art.className='subject-background-art';document.querySelector('main').append(art);}
}
if(searchType==='term' || searchType==='date'){
  $("query").placeholder=searchType==='term'?"Начни вводить термин":"Начни вводить дату или событие";
  const label=node('label',searchType==='term'?'Найти термин по названию':'Найти дату или событие','term-search-label');label.htmlFor='query';$('search-form').before(label);document.body.classList.add('term-search-page');
}
document.title=`${$("page-title").textContent} — Олеся Блок`;
$("query").value=query;
$("search-subject").value=subject || "";
$("search-subject-label").hidden=path === "/" || Boolean(subject);
const crumbs=$("breadcrumbs");const home=node("a","Главная");home.href="/";crumbs.append(home);
if(subject){const a=node("a",subject === "history" ? "История" : "Обществознание");a.href=`/${subject}`;crumbs.append(node("span","›"),a);}
if(route.title || path === "/search"){const current=node("span",route.title || "Результаты поиска");current.setAttribute("aria-current","page");crumbs.append(node("span","›"),current);}
if(path === "/")crumbs.hidden=true;
$("search-subject").addEventListener("change",()=>{
  $("query").dispatchEvent(new Event("input"));
  if(path === "/search" && query){const url=new URL(location.href);url.searchParams.set("subject",$("search-subject").value);history.replaceState(null,"",url);load();}
});
function card(item){
  const preview=value=>{const text=String(value||'').trim();return text.length>120 ? text.slice(0,117).trimEnd()+'…' : text;};
  const b=node("article",null,`item item-${item.type} ${item.subject}-item`);b.tabIndex=0;b.setAttribute('role','button');b.addEventListener('keydown',event=>{if(event.target===b && ['Enter',' '].includes(event.key)){event.preventDefault();if(path!=='/profile')recordSearchSelection(item,false);openMaterial(item.id);}});
  b.append(node("small",[types[item.type] || "Материал", item.section?.title].filter(Boolean).join(" · ")),node("strong",item.type === "date" ? preview(item.eventText || item.title) : item.title));
  if(item.type === "date" && item.dateLabel)b.append(node("span",item.dateLabel,"date-label"));
  if(item.type !== "date" && (item.definition || item.summary))b.append(node("p",preview(item.definition || item.summary),"card-excerpt"));
  if(item.type === "term" || item.type === "plan"){
    const decoration=toolIcon(item.type === "plan" ? "Планы" : "Термины");decoration.classList.add("card-decoration");decoration.setAttribute("aria-hidden","true");b.append(decoration,node("span","Подробнее →","read-more"));
  }
  const heart=node('button','♡','favorite-heart'),notice=node('span',null,'sr-only');heart.type='button';heart.setAttribute('aria-label','Добавить в избранное');heart.addEventListener('click',event=>event.stopPropagation());b.append(heart,notice);window.showFavorite(item.id,heart,notice);
  if(item.sources?.length){const source=node('small',[...new Set(item.sources)].join('; '),'card-source');b.querySelector('strong').before(source);}
  if(item.type==='date'){const date=b.querySelector('.date-label'),title=b.querySelector('strong');if(date){date.className='favorite-date-title';title.before(date);}title.replaceWith(node('p',title.textContent,'card-excerpt date-event'));}
  b.addEventListener("click",()=>{if(path!=='/profile')recordSearchSelection(item,false);openMaterial(item.id);});return b;
}
async function load(reset=true){
  $("material").hidden=true;detailSequence++;
  if(reset){controller?.abort();cursor=null;loaded=0;$("items").replaceChildren();}
  const run=++sequence;controller=new AbortController();
  $("catalog").hidden=false;$("retry").hidden=true;$("more").hidden=true;$("status").textContent="Загружаем материалы…";$("items").setAttribute("aria-busy","true");
  const params=new URLSearchParams({limit:"50"});
  const selected=subject && path !== "/search" ? subject : $("search-subject").value;
  if(query && !selected){$("status").textContent="Выбери предмет для поиска.";$("items").removeAttribute("aria-busy");return;}
  if(selected)params.set("subject",selected);
  if(searchType)params.set("type",searchType);
  if(!query){
    if(subject)params.set("subject",subject);
    if(route.type)params.set("type",route.type);
    if($("section").value)params.set("sectionId",$("section").value);
  }
  if(cursor)params.set("cursor",cursor);if(query)params.set("q",query);
  try{
    const result=await api(`${query ? "/search" : "/content"}?${params}`,controller.signal);
    if(run !== sequence)return;
    result.items.forEach(item=>$("items").append(card(item)));if(query)ensureReferenceList();loaded+=result.items.length;cursor=result.page?.nextCursor;
    $("more").hidden=!result.page?.hasMore;$("status").textContent=loaded ? "" : "Материалы не найдены. Попробуй изменить запрос или фильтр.";
  }catch(error){if(run !== sequence || error.name === "AbortError")return;$("status").textContent=error.message;$("retry").hidden=false;$("retry").onclick=()=>load(reset);}
  finally{if(run === sequence)$("items").removeAttribute("aria-busy");}
}
async function sections(){
  if(!subject || path === "/search"){$("filter-label").hidden=true;return;}
  const isHistory=subject === "history";
  $("filter-label").firstChild.textContent=isHistory ? "Период " : "Раздел ";
  $("section").firstChild.textContent=isHistory ? "Все периоды" : "Все разделы";
  try{
    const result=await api(`/subjects/${subject}/sections`);
    function add(items, depth=0){items.forEach(item=>{if(!/^таблиц/i.test(item.title)){const option=node("option",`${"— ".repeat(depth)}${item.title}`);option.value=item.id;$("section").append(option);}add(item.children || [],depth+1);});}add(result.items);
  }catch{$("filter-label").append(node("small","Фильтр не загрузился. Обнови страницу."));}
}
$("search-form").addEventListener("submit",event=>{
  event.preventDefault();query=$("query").value.trim();
  const selected=subject && path !== "/search" ? subject : $("search-subject").value;
  if(!selected){$("search-subject").focus();$("global-search-status").textContent="Выбери предмет для поиска.";return;}
  if(path !== "/search"){
    if(!query){$("query").focus();return;}
    location.assign(`/search?${new URLSearchParams({q:query,subject:selected,...(searchType?{type:searchType}:{})})}`);return;
  }
  const url=new URL(location.href);url.searchParams.set("subject",selected);query ? url.searchParams.set("q",query) : url.searchParams.delete("q");
  history.replaceState(null,"",url);load();
});
$("section").addEventListener("change",()=>load());$("more").addEventListener("click",()=>load(false));
function view(list){$("items").className=list ? "list" : "cards";if($("reference-browse-items"))$("reference-browse-items").className=list ? "list" : "cards";$("list-view").setAttribute("aria-pressed",String(list));$("cards-view").setAttribute("aria-pressed",String(!list));try{localStorage.setItem("referenceView",list ? "list" : "cards");}catch{}}
$("list-view").onclick=()=>view(true);$("cards-view").onclick=()=>view(false);
try{const saved=localStorage.getItem("referenceView");view(saved ? saved === "list" : route.type === "date");}catch{}
function sourceLine(value){return node("p",`Источник: ${value}`,"material-source");}
function plan(items, nested=false){
  const list=node(nested ? "ul" : "ol");
  items.forEach(item=>{
    const li=node("li");li.append(node("span",item.text));
    if(item.required){li.classList.add("required-point");li.append(node("span","Обязательный пункт","required-badge"));}
    if(item.children?.length)li.append(plan(item.children,true));
    list.append(li);
  });return list;
}
function recordSearchSelection(item,analytics=true){
  window.accountIdentity().then(user=>{if(user||analytics)return accountJson(user?'/me/search-history':'/search/selection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contentId:item.id,query:item.title,analytics})});}).catch(()=>{$('global-search-status').textContent='Материал открыт, но запрос не удалось сохранить в историю.';});
}
function selectSuggestedMaterial(item){
  recordSearchSelection(item);openMaterial(item.id);ensureReferenceList(item.type);
}
let referenceListKey='',referenceListRevision=0;
async function ensureReferenceList(type=searchType){
  if(!subject || path==='/profile')return;
  if(route.type && !query){$('catalog').hidden=false;return;}
  const key=`${subject}-${type||'all'}`;if(referenceListKey===key)return;referenceListKey=key;const revision=++referenceListRevision;
  document.getElementById('reference-browse')?.remove();
  const section=node('section',null,'reference-browse');section.id='reference-browse';
  const title={term:'Все термины',date:'Все даты',plan:'Все планы'}[type]||'Все материалы предмета';section.append(node('h2',title));
  const list=node('div',null,$('items').className);list.id='reference-browse-items';const status=node('p'),more=node('button','Показать ещё 50');more.type='button';more.hidden=true;section.append(list,status,more);$('catalog').after(section);let next=null;
  async function browse(){more.disabled=true;status.textContent='Загружаем справочник…';try{const params=new URLSearchParams({subject,limit:'50',...(type?{type}:{}),...(next?{cursor:next}:{})});const data=await api(`/content?${params}`);if(revision!==referenceListRevision)return;data.items.forEach(item=>list.append(card(item)));next=data.page?.nextCursor;more.hidden=!data.page?.hasMore;status.textContent=list.children.length?'':'Материалов пока нет.';}catch(e){status.textContent=e.message;more.hidden=false;more.textContent='Повторить';}finally{more.disabled=false;}}
  more.onclick=browse;await browse();
}
const favoriteDialog=document.createElement('dialog');favoriteDialog.className='favorite-material-dialog';favoriteDialog.setAttribute('aria-labelledby','material-title');document.body.append(favoriteDialog);
favoriteDialog.addEventListener('click',event=>{if(event.target!==favoriteDialog)return;const box=favoriteDialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)favoriteDialog.close();});
favoriteDialog.addEventListener('close',()=>{document.body.classList.remove('favorite-modal-open');$('material').hidden=true;$('material').dispatchEvent(new Event('close'));});
async function openMaterial(id){
  const run=++detailSequence;lastFocus=document.activeElement;
  $("favorite-material").hidden=true;$("favorite-status").textContent="";
  $("material-title").textContent="Загружаем…";$("material-body").replaceChildren();$("material-meta").textContent="";$("material-source").textContent="";$("related").replaceChildren();$("related").hidden=true;
  const inCabinet=path==='/profile';
  if(inCabinet){favoriteDialog.append($('material'));$('material').hidden=false;if(!favoriteDialog.open)favoriteDialog.showModal();document.body.classList.add('favorite-modal-open');}
  else{$('catalog').before($('material'));$('material').hidden=false;$('material').scrollIntoView({block:'nearest',behavior:'smooth'});}
  try{
    const item=await api(`/content/${encodeURIComponent(id)}`);if(run !== detailSequence || $("material").hidden)return;
    $("material").className=`inline-material ${item.subject}-material ${item.type}-material`;$("material-title").textContent=item.title;$("material-title").before($("material-source"));$("material-meta").textContent=[types[item.type],item.section?.title].filter(Boolean).join(" · ");
    window.showFavorite(item.id);
    if(window.mountPromotion)window.mountPromotion($('material'),'material',item.subject);
    const d=item.details || {},body=$("material-body");
    if(d.kind === "date"){body.append(node("h3",d.dateLabel),node("p",d.eventText));}
    else if(d.kind === "term"){
      const features=d.features || [];
      const plain=value=>String(value||'').replace(/[•●▪]/g,'').replace(/\s+/g,' ').trim();
      const featureText=plain(features.map(f=>f.text).join(' '));
      if(d.definition && (!features.length || (item.subject!=='society' && plain(d.definition)!==featureText && !featureText.includes(plain(d.definition)))))body.append(node('p',d.definition));
      if(features.length){const list=node('ul',null,'term-features');features.forEach(f=>list.append(node('li',f.text)));body.append(list);}
    }
    else if(d.kind === "plan"){if(d.introduction)body.append(node("p",d.introduction));body.append(plan(d.items || []));(d.requirementNotes||[]).forEach(note=>body.append(node("p",note,"required-plan-note")));if(d.conclusion)body.append(node("p",d.conclusion));}
    else body.append(node("p",typeof d.body === "string" ? d.body : item.summary || ""));
    const sources=[...new Set([...(item.sources||[]),...(d.features||[]).map(f=>f.source)].filter(Boolean))];
    if(d.kind==='term' || d.kind==='plan')$('material-source').textContent=sources.length ? sources.join('; ') : '';
    else (item.sources || []).forEach(value=>body.append(sourceLine(value)));
    (item.assets || []).forEach(asset=>{const url=new URL(asset.url,location.origin);if(url.origin !== location.origin || !url.pathname.startsWith("/media/"))return;if(asset.mimeType?.startsWith("image/")){const img=node("img");img.src=url.href;img.alt=asset.altText || item.title;body.append(img);}});
    try{const related=await api(`/content/${encodeURIComponent(id)}/related`);if(run !== detailSequence || $("material").hidden)return;if(related.content?.length){$("related").hidden=false;$("related").append(node("h3","Связанные материалы"));related.content.forEach(({item:other})=>{const b=node("button",other.title);b.onclick=()=>openMaterial(other.id);$("related").append(b);});}}catch{}
  }catch(error){if(run === detailSequence){$("material-title").textContent="Не удалось открыть материал";$("material-body").append(node("p",error.message));}}
}
$("close-material").onclick=()=>{if(favoriteDialog.open){favoriteDialog.close();return;}$("material").hidden=true;$("material").dispatchEvent(new Event("close"));};
$("material").addEventListener("close",()=>{detailSequence++;if(lastFocus?.isConnected)lastFocus.focus();});
sections();if(path === "/search"){
  $("page-title").textContent=query ? `Результаты поиска: «${query}»` : "Поиск по всем материалам";
  $("intro").textContent="Поиск по выбранному предмету. Термины ищутся по названию, даты — по дате и событию, планы — по теме.";
}
if(route.type || path === "/search" || (query && path !== "/" && path !== "/profile"))load();

// Title-only autocomplete complements the global content search.
if(path === "/society/plans"){
  $("plan-search").hidden=false;
  $("search-form").hidden=true;$("search-subject-label").hidden=true;
  document.querySelector(".search-heading").hidden=true;
  const input=$("plan-query"),popup=$("plan-suggestions"),status=$("plan-search-status");
  $('plan-search-form').addEventListener('submit',event=>{event.preventDefault();const text=input.value.trim();if(text)location.assign(`/search?${new URLSearchParams({q:text,subject:'society',type:'plan'})}`);});
  let timer,request,revision=0,options=[],active=-1;
  function closeSuggestions(){popup.hidden=true;input.setAttribute("aria-expanded","false");input.removeAttribute("aria-activedescendant");active=-1;}
  function selectActive(index){active=index;options.forEach((option,i)=>option.setAttribute("aria-selected",String(i===index)));if(index>=0){input.setAttribute("aria-activedescendant",options[index].id);options[index].scrollIntoView({block:"nearest"});}else input.removeAttribute("aria-activedescendant");}
  input.addEventListener("input",()=>{
    clearTimeout(timer);request?.abort();const run=++revision;closeSuggestions();options=[];
    const text=input.value.trim();
    if(text.length<2){status.textContent=text ? "Введи минимум 2 символа." : "";return;}
    status.textContent="Ищем подходящие планы…";
    timer=setTimeout(async()=>{
      request=new AbortController();
      try{
        const result=await api(`/plans/suggestions?${new URLSearchParams({q:text,limit:"8"})}`,request.signal);
        if(run!==revision || document.activeElement!==input)return;
        popup.replaceChildren();options=[];
        result.items.forEach((item,index)=>{
          const option=node("div",null,"plan-option");option.id=`plan-option-${index}`;option.setAttribute("role","option");option.setAttribute("aria-selected","false");
          option.append(node("strong",item.title),node("small",item.section?.title || "План по обществознанию"));
          option.addEventListener("mousedown",event=>event.preventDefault());
          option.addEventListener("click",()=>{input.value=item.title;closeSuggestions();selectSuggestedMaterial(item);});
          options.push(option);popup.append(option);
        });
        popup.hidden=!options.length;input.setAttribute("aria-expanded",String(Boolean(options.length)));
        status.textContent=options.length ? "Выбери план из подсказок." : "По этому названию планы не найдены. Попробуй другое слово.";
      }catch(error){if(run===revision && error.name!=="AbortError")status.textContent="Подсказки не загрузились. Попробуй ввести название ещё раз.";}
    },250);
  });
  input.addEventListener("keydown",event=>{
    if(event.key==="Escape"){revision++;request?.abort();closeSuggestions();return;}
    if(event.key==='Enter'){event.preventDefault();$('plan-search-form').requestSubmit();return;}
    if(popup.hidden || !options.length)return;
    if(event.key==="ArrowDown"){event.preventDefault();selectActive((active+1)%options.length);}
    else if(event.key==="ArrowUp"){event.preventDefault();selectActive(active<0 ? options.length-1 : (active-1+options.length)%options.length);}
    else if(event.key==="Enter"){event.preventDefault();if(active>=0)options[active].click();}
  });
  input.addEventListener("blur",()=>{revision++;clearTimeout(timer);request?.abort();closeSuggestions();});
  status.textContent="";
}

{
  const input=$("query"),popup=$("global-suggestions"),status=$("global-search-status");
  let timer,request,revision=0,options=[],active=-1;
  function close(){popup.hidden=true;input.setAttribute("aria-expanded","false");input.removeAttribute("aria-activedescendant");active=-1;}
  input.addEventListener("input",()=>{
    clearTimeout(timer);request?.abort();const run=++revision;close();options=[];
    const text=input.value.trim();const selected=subject && path !== "/search" ? subject : $("search-subject").value;
    if(!selected){status.textContent="Сначала выбери предмет для поиска.";return;}
    if(text.length<2){status.textContent="Введи минимум 2 символа для подсказок.";return;}
    timer=setTimeout(async()=>{
      request=new AbortController();
      try{
        const result=await api(`/search/suggestions?${new URLSearchParams({q:text,limit:"8",subject:selected,...(searchType?{type:searchType}:{})})}`,request.signal);
        if(run!==revision || document.activeElement!==input)return;
        popup.replaceChildren();options=[];
        result.items.forEach((item,index)=>{
          const option=node("div",null,"plan-option");option.id=`global-option-${index}`;option.setAttribute("role","option");option.setAttribute("aria-selected","false");
          const metadata=[types[item.type],item.subject === "history" ? "История" : "Обществознание",item.section?.title].filter(Boolean).join(" · ");
          option.append(node("strong",item.title),node("small",metadata));
          if(item.summary)option.append(node("p",item.summary,"suggestion-excerpt"));
          option.addEventListener("mousedown",event=>event.preventDefault());
          option.addEventListener("click",()=>{input.value=item.title;close();selectSuggestedMaterial(item);});
          options.push(option);popup.append(option);
        });
        popup.hidden=!options.length;input.setAttribute("aria-expanded",String(Boolean(options.length)));
        status.textContent=options.length ? `Подсказок: ${options.length}. Выбери материал или нажми «Найти» для всех результатов.` : "Подсказок нет. Нажми «Найти» для поиска всех результатов.";
      }catch(error){if(run===revision && error.name!=="AbortError")status.textContent="Подсказки недоступны. Можно воспользоваться кнопкой «Найти».";}
    },250);
  });
  input.addEventListener("keydown",event=>{
    if(event.key==="Escape"){revision++;request?.abort();close();return;}
    if(event.key==='Enter'){event.preventDefault();$('search-form').requestSubmit();return;}
    if(popup.hidden || !options.length)return;
    if(event.key==="ArrowDown" || event.key==="ArrowUp"){
      event.preventDefault();active=event.key==="ArrowDown" ? (active+1)%options.length : (active<0 ? options.length-1 : (active-1+options.length)%options.length);
      options.forEach((option,i)=>option.setAttribute("aria-selected",String(i===active)));input.setAttribute("aria-activedescendant",options[active].id);options[active].scrollIntoView({block:"nearest"});
    }else if(event.key==="Enter" && active>=0){event.preventDefault();options[active].click();}
  });
  input.addEventListener("blur",()=>{revision++;clearTimeout(timer);request?.abort();close();});
  $("search-form").addEventListener("submit",()=>{revision++;clearTimeout(timer);request?.abort();close();});
}
