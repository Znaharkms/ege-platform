from datetime import datetime, date, timedelta
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import text
from app.api.dependencies import AdminPrincipal
from app.api.routes.catalog import Session
from app.core.problems import ProblemException

router=APIRouter(tags=['Promotions'])
Placement=Literal['home','history','society','material','result','cabinet']
class Promotion(BaseModel):
 name:str=Field(min_length=1,max_length=120)
 title:str=Field(min_length=1,max_length=160)
 body:str=Field('',max_length=1500)
 button:str=Field('Подробнее',min_length=1,max_length=60)
 url:str=Field(max_length=2000)
 image:str=Field('',max_length=500)
 subject:Literal['history','society','both']='both'
 placements:list[Placement]=Field(min_length=1,max_length=6)
 status:Literal['draft','active','off']='draft'
 priority:int=Field(0,ge=0,le=1000)
 starts_at:datetime|None=None
 ends_at:datetime|None=None
 @model_validator(mode='after')
 def validate_fields(self):
  for key in ('name','title','button'):
   value=getattr(self,key).strip()
   if not value:raise ValueError('Заполните название, заголовок и кнопку')
   setattr(self,key,value)
  parsed=urlsplit(self.url)
  if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username:raise ValueError('Ссылка должна начинаться с https:// или http://')
  if self.image and (not self.image.startswith('/media/') or '..' in self.image or '?' in self.image):raise ValueError('Загрузите картинку через редактор')
  for d in (self.starts_at,self.ends_at):
   if d and d.tzinfo is None:raise ValueError('Укажите часовой пояс расписания')
  if self.starts_at and self.ends_at and self.ends_at<=self.starts_at:raise ValueError('Окончание должно быть позже начала')
  self.placements=list(dict.fromkeys(self.placements))
  return self

SELECT="""SELECT p.*, (SELECT count(*) FROM app.promotion_events e WHERE e.promotion_id=p.id AND e.kind='view') AS views,
 (SELECT count(*) FROM app.promotion_events e WHERE e.promotion_id=p.id AND e.kind='click') AS clicks FROM app.promotions p"""
@router.get('/admin/promotions',include_in_schema=False)
async def admin_list(admin:AdminPrincipal,session:Session):
 result=await session.execute(text(SELECT+' ORDER BY p.updated_at DESC,p.id'))
 return {'items':[dict(r) for r in result.mappings()]}

@router.get('/admin/promotions/statistics',include_in_schema=False)
async def statistics(admin:AdminPrincipal,session:Session,start:date,end:date):
 if end<start or (end-start).days>36500:
  raise ProblemException(422,'invalid_period','Выберите корректный период')
 previous=start-timedelta(days=(end-start).days+1)
 campaigns=await session.execute(text('SELECT id,name,status FROM app.promotions ORDER BY name,id'))
 items={str(r['id']):{'id':r['id'],'name':r['name'],'status':r['status'],'views':0,'clicks':0,'previousViews':0,'previousClicks':0,'placements':[]} for r in campaigns.mappings()}
 result=await session.execute(text("""SELECT promotion_id,placement,(day>=:start) AS current,
 count(*) FILTER(WHERE kind='view') AS views,count(*) FILTER(WHERE kind='click') AS clicks
 FROM app.promotion_events WHERE day>=:previous AND day<=:end
 GROUP BY promotion_id,placement,(day>=:start)"""),{'start':start,'previous':previous,'end':end})
 placements={}
 for row in result.mappings():
  item=items.get(str(row['promotion_id']))
  if item is None:continue
  key=(str(row['promotion_id']),row['placement'])
  place=placements.setdefault(key,{'placement':row['placement'],'views':0,'clicks':0,'previousViews':0,'previousClicks':0})
  for metric in ('views','clicks'):
   field=metric if row['current'] else 'previous'+metric.title()
   item[field]+=row[metric];place[field]+=row[metric]
 for (id,placement),value in placements.items():items[id]['placements'].append(value)
 def rates(item):
  item['ctr']=round(item['clicks']/item['views']*100,2) if item['views'] else None
  item['previousCtr']=round(item['previousClicks']/item['previousViews']*100,2) if item['previousViews'] else None
  item['ctrChange']=round(item['ctr']-item['previousCtr'],2) if item['ctr'] is not None and item['previousCtr'] is not None else None
 for item in items.values():
  rates(item)
  for place in item['placements']:rates(place)
 return {'start':start,'end':end,'previousStart':previous,'previousEnd':start-timedelta(days=1),'items':list(items.values())}


@router.post('/admin/promotions',status_code=201,include_in_schema=False)
async def create(payload:Promotion,admin:AdminPrincipal,session:Session):
 data=payload.model_dump();result=await session.execute(text("""INSERT INTO app.promotions(name,title,body,button,url,image,subject,placements,status,priority,starts_at,ends_at)
 VALUES(:name,:title,:body,:button,:url,:image,:subject,CAST(:placements AS text[]),:status,:priority,:starts_at,:ends_at) RETURNING id"""),data)
 row=result.scalar_one();await session.commit();return {'id':row}

@router.put('/admin/promotions/{id}',include_in_schema=False)
async def update(id:UUID,payload:Promotion,admin:AdminPrincipal,session:Session):
 data=payload.model_dump();data['id']=id
 result=await session.execute(text("""UPDATE app.promotions SET name=:name,title=:title,body=:body,button=:button,url=:url,image=:image,
 subject=:subject,placements=CAST(:placements AS text[]),status=:status,priority=:priority,starts_at=:starts_at,ends_at=:ends_at,updated_at=now()
 WHERE id=:id RETURNING id"""),data)
 if result.scalar_one_or_none() is None:raise ProblemException(404,'promotion_missing','Карточка не найдена')
 await session.commit();return {'id':id}

@router.delete('/admin/promotions/{id}',status_code=204,include_in_schema=False)
async def delete(id:UUID,admin:AdminPrincipal,session:Session):
 result=await session.execute(text('DELETE FROM app.promotions WHERE id=:id RETURNING id'),{'id':id})
 if result.scalar_one_or_none() is None:raise ProblemException(404,'promotion_missing','Карточка не найдена')
 await session.commit()

ACTIVE="status='active' AND (starts_at IS NULL OR starts_at<=now()) AND (ends_at IS NULL OR ends_at>now())"
@router.get('/promotions',include_in_schema=False)
async def public_list(session:Session,placement:Placement,subject:Literal['history','society']|None=None):
 result=await session.execute(text("""SELECT id,title,body,button,image,subject FROM app.promotions WHERE """+ACTIVE+"""
 AND :placement=ANY(placements) AND (CAST(:subject AS text) IS NULL OR subject='both' OR subject=:subject)
 ORDER BY priority DESC,updated_at DESC,id LIMIT 1"""),{'placement':placement,'subject':subject})
 return {'items':[dict(r) for r in result.mappings()]}

class Impression(BaseModel):
 visitor:UUID
 placement:Placement

async def record(session,id,visitor,placement,kind):
 result=await session.execute(text('SELECT url FROM app.promotions WHERE id=:id AND '+ACTIVE+' AND :placement=ANY(placements)'),{'id':id,'placement':placement})
 url=result.scalar_one_or_none()
 if url is None:raise ProblemException(404,'promotion_inactive','Предложение больше недоступно')
 if kind=='click':
  # A click confirms an impression even if IntersectionObserver delivery was missed.
  await session.execute(text("""INSERT INTO app.promotion_events(promotion_id,visitor,placement,kind) VALUES(:id,:visitor,:placement,'view') ON CONFLICT DO NOTHING"""),{'id':id,'visitor':visitor,'placement':placement})
 await session.execute(text("""INSERT INTO app.promotion_events(promotion_id,visitor,placement,kind) VALUES(:id,:visitor,:placement,:kind) ON CONFLICT DO NOTHING"""),{'id':id,'visitor':visitor,'placement':placement,'kind':kind})
 await session.commit();return url

@router.post('/promotions/{id}/view',status_code=204,include_in_schema=False)
async def view(id:UUID,payload:Impression,session:Session):
 await record(session,id,payload.visitor,payload.placement,'view')

@router.get('/promotions/{id}/go',include_in_schema=False)
async def click(id:UUID,visitor:UUID,placement:Placement,session:Session):
 return RedirectResponse(await record(session,id,visitor,placement,'click'),status_code=302)
