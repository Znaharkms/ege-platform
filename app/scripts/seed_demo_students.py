"""Add or remove exactly 30 synthetic accounts; never sends email."""
import argparse
import asyncio
import json
from datetime import UTC,datetime,timedelta
from uuid import UUID,uuid5
from sqlalchemy import text
from app.db.session import SessionFactory

NAMESPACE=UUID('b30221ec-23de-4b91-a590-82e99be02712')
NAMES=['Анна','Иван','Мария','Алексей','Дарья','Максим','Екатерина','Дмитрий','Софья','Артём']
def demo_id(number):return uuid5(NAMESPACE,f'student-{number}')
def email(number):return f'demo.student{number:02d}@example.invalid'

async def run(clear=False):
 now=datetime.now(UTC)
 async with SessionFactory.begin() as session:
  # Serialize repeated invocations, including cleanup.
  await session.execute(text('SELECT pg_advisory_xact_lock(23053530)'))
  existing=await session.execute(text('SELECT id,email,role::text AS role FROM app.users WHERE id=ANY(CAST(:ids AS uuid[])) FOR UPDATE'),{'ids':[str(demo_id(i)) for i in range(1,31)]})
  found={r['id']:dict(r) for r in existing.mappings()}
  for i in range(1,31):
   row=found.get(demo_id(i))
   if row and (row['email']!=email(i) or row['role']!='student'):
    raise RuntimeError('Тестовый ID занят другим аккаунтом. Операция отменена.')
  if clear:
   ids=[str(x) for x in found]
   await session.execute(text('DELETE FROM app.question_error_reports WHERE user_id=ANY(CAST(:ids AS uuid[]))'),{'ids':ids})
   await session.execute(text('DELETE FROM app.test_attempts WHERE user_id=ANY(CAST(:ids AS uuid[]))'),{'ids':ids})
   await session.execute(text('DELETE FROM app.users WHERE id=ANY(CAST(:ids AS uuid[]))'),{'ids':ids})
   print(f'Удалено тестовых учеников: {len(ids)}. Реальные аккаунты не затронуты.')
   return
  result=await session.execute(text("SELECT id,code FROM app.subjects WHERE code IN ('history','society')"))
  subjects={r['code']:r['id'] for r in result.mappings()}
  if len(subjects)!=2:raise RuntimeError('В базе должны быть предметы history и society.')
  pools={};tests={};content={}
  for code,sid in subjects.items():
   result=await session.execute(text("""SELECT q.id,q.current_version FROM app.questions q
     WHERE q.subject_id=:sid AND q.status='published' ORDER BY q.id LIMIT 10"""),{'sid':sid})
   pools[code]=[dict(r) for r in result.mappings()]
   result=await session.execute(text("SELECT id FROM app.tests WHERE subject_id=:sid AND status='published' ORDER BY (slug='history-trainer') DESC,id LIMIT 1"),{'sid':sid})
   tests[code]=result.scalar_one_or_none()
   result=await session.execute(text("SELECT id,title,content_type_code FROM app.content_items WHERE subject_id=:sid AND status='published' AND content_type_code IN ('term','date','plan') ORDER BY id LIMIT 8"),{'sid':sid})
   content[code]=[dict(r) for r in result.mappings()]
  created=0;attempts=0;reports=0
  for i in range(1,31):
   uid=demo_id(i)
   if uid in found:continue
   age=[3,7,14,30,60,100][(i-1)%6]
   inactivity=[0,2,8,20,45][(i-1)%5]
   registered=now-timedelta(days=age)
   codes=['history'] if i<=10 else ['society'] if i<=20 else ['history','society']
   status='blocked' if i%6==0 else 'active'
   await session.execute(text("""INSERT INTO app.users(id,email,display_name,role,status,created_at,updated_at,last_login_at)
     VALUES(:id,:email,:name,'student',CAST(:status AS app.account_status),:registered,:now,:login)"""),
     {'id':uid,'email':email(i),'name':f'[ТЕСТ] {NAMES[(i-1)%10]} {i:02d}','status':status,'registered':registered,'now':now,'login':now-timedelta(days=min(age,inactivity))})
   await session.execute(text("""INSERT INTO app.student_admin_profiles(user_id,notes,groups,subjects)
     VALUES(:id,:notes,CAST(:groups AS text[]),CAST(:subjects AS text[]))"""),
     {'id':uid,'notes':f'Тестовый ученик №{i}. Удаляется командой seed_demo_students --clear. Сценарий: возраст аккаунта {age} дней, перерыв {inactivity} дней.',
      'groups':['Тестовые ученики', '10 класс' if i%2 else '11 класс', 'ЕГЭ 2027'],'subjects':codes})
   created+=1
   for code in codes:
    # Some accounts intentionally have no training history.
    if inactivity<age and i%10!=0:
     offsets=list(range(inactivity,age,max(1,age//8)))[:12]
     for j,days_ago in enumerate(reversed(offsets)):
      aid=uuid5(NAMESPACE,f'attempt-{i}-{code}-{j}');started=now-timedelta(days=days_ago,hours=1)
      pool=pools[code];count=len(pool) or 10
      threshold=min(9,2+(i%5)+j//2)
      correct=sum((i+j+k)%10<threshold for k in range(count))
      await session.execute(text("""INSERT INTO app.test_attempts(id,user_id,test_id,subject_id,status,test_title_snapshot,score,max_score,correct_count,question_count,started_at,last_activity_at,completed_at)
       VALUES(:id,:user,:test,:subject,'completed',:title,:score,:maximum,:correct,:count,:started,:ended,:ended)"""),
       {'id':aid,'user':uid,'test':tests[code],'subject':subjects[code],'title':f'[ТЕСТ] {"История" if code=="history" else "Обществознание"} · тренировка {j+1}',
        'score':correct,'maximum':count,'correct':correct,'count':count,'started':started,'ended':started+timedelta(minutes=25)})
      attempts+=1
      for k,q in enumerate(pool):
       aqid=uuid5(NAMESPACE,f'question-{aid}-{k}');ok=(i+j+k)%10<threshold
       await session.execute(text("""INSERT INTO app.attempt_questions(id,attempt_id,question_id,question_version,position,points_possible)
        VALUES(:id,:attempt,:question,:version,:position,1)"""),{'id':aqid,'attempt':aid,'question':q['id'],'version':q['current_version'],'position':k+1})
       await session.execute(text("""INSERT INTO app.attempt_answers(attempt_question_id,response,is_correct,points_awarded,feedback,answered_at)
        VALUES(:id,CAST(:response AS jsonb),:ok,:points,'{}'::jsonb,:at)"""),
        {'id':aqid,'response':json.dumps({'text':'Синтетический ответ для тестирования'}),'ok':ok,'points':1 if ok else 0,'at':started+timedelta(minutes=k+1)})
    for k,item in enumerate(content[code][:3]):
     when=registered+timedelta(hours=k+1)
     await session.execute(text("""INSERT INTO app.search_history(user_id,query,subject_id,filters,selected_content_id,created_at)
      VALUES(:id,:query,:subject,CAST(:filters AS jsonb),:content,:at)"""),
      {'id':uid,'query':item['title'],'subject':subjects[code],'filters':json.dumps({'type':item['content_type_code']}),'content':item['id'],'at':when})
     if i%3!=0:
      await session.execute(text('INSERT INTO app.favorites(user_id,content_id,created_at) VALUES(:id,:content,:at) ON CONFLICT DO NOTHING'),{'id':uid,'content':item['id'],'at':when})
   if i%3==0:
    resolved=i%9==0;workflow='resolved' if resolved else 'in_progress' if i%2==0 else 'new'
    await session.execute(text("""INSERT INTO app.question_error_reports(user_id,message,status,workflow_status,admin_reply,created_at,updated_at,replied_at,student_viewed_at)
     VALUES(:id,:message,:status,:workflow,:reply,:at,:at,:replied,NULL)"""),
     {'id':uid,'message':f'[ТЕСТ] Обращение ученика {i}: помогите разобраться с результатом тренировки. Это вымышленное сообщение.',
      'status':'answered' if resolved else 'open','workflow':workflow,'reply':'[ТЕСТ] Ответ администратора: проверьте пояснение к заданию.' if resolved else None,'at':registered+timedelta(days=1),'replied':registered+timedelta(days=1,hours=1) if resolved else None})
    reports+=1
  print(f'Добавлено учеников: {created}; уже существовало: {len(found)}; тренировок: {attempts}; обращений: {reports}.')
  for code,pool in pools.items():
   if not pool:print(f'{code}: нет опубликованных вопросов. Баллы заполнены, но по этому предмету нет ответов для анализа слабых тем.')
  print('Почта не отправлялась. Тестовые ученики помечены [ТЕСТ] и группой «Тестовые ученики».')

if __name__=='__main__':
 parser=argparse.ArgumentParser(description='30 вымышленных учеников для проверки админ-панели')
 parser.add_argument('--clear',action='store_true',help='Удалить только эти тестовые аккаунты и их учебные данные')
 args=parser.parse_args();asyncio.run(run(args.clear))
