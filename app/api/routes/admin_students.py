"""Student directory; every operation requires an administrator session."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.api.dependencies import AdminPrincipal
from app.api.routes.catalog import Session
from app.core.problems import ProblemException

router = APIRouter(prefix='/admin/students', tags=['Students'])

STUDENT_SELECT = """
SELECT u.id, u.display_name AS name, u.email, u.status::text AS status,
       u.created_at AS "registeredAt", coalesce(p.notes,'') AS notes,
       coalesce(p.groups,'{}'::text[]) AS groups,
       CASE WHEN cardinality(p.subjects)>0 THEN p.subjects ELSE ARRAY(
         SELECT DISTINCT s.code FROM app.subjects s WHERE s.id IN (
           SELECT a.subject_id FROM app.test_attempts a WHERE a.user_id=u.id
           UNION SELECT h.subject_id FROM app.search_history h WHERE h.user_id=u.id)
         ORDER BY s.code) END AS subjects,
       (SELECT max(greatest(a.last_activity_at,a.completed_at,a.started_at)) FROM app.test_attempts a WHERE a.user_id=u.id) AS "lastPractice",

       greatest(u.last_login_at,
         (SELECT max(s.last_used_at) FROM app.sessions s WHERE s.user_id=u.id),
         (SELECT max(h.created_at) FROM app.search_history h WHERE h.user_id=u.id),
         (SELECT max(greatest(a.completed_at,a.last_activity_at,a.started_at)) FROM app.test_attempts a WHERE a.user_id=u.id),
         (SELECT max(m.created_at) FROM app.question_error_reports m WHERE m.user_id=u.id)) AS "lastActivity"
FROM app.users u LEFT JOIN app.student_admin_profiles p ON p.user_id=u.id
"""


FILTER = """
 WHERE u.role='student' AND u.status<>'deleted'
 AND (:q='' OR strpos(lower(coalesce(u.display_name,'')),lower(:q))>0 OR strpos(lower(coalesce(u.email,'')),lower(:q))>0)
 AND (CAST(:status AS text) IS NULL OR u.status::text=:status)
 AND (:group_name='' OR :group_name=ANY(coalesce(p.groups,'{}'::text[])))
"""

async def filtered_students(session, q='', status=None, subject=None, group='', inactive_days=0, offset=0, limit=None):
    result=await session.execute(text('SELECT *,count(*) OVER() AS total FROM ('+STUDENT_SELECT+FILTER+"""
    ) directory WHERE
      (CAST(:subject AS text) IS NULL
       OR (:subject='both' AND subjects @> ARRAY['history','society']::text[])
       OR (:subject<>'both' AND subjects=ARRAY[CAST(:subject AS text)]))
      AND (:inactive_days=0 OR coalesce("lastPractice","registeredAt")<now()-make_interval(days=>:inactive_days))
    ORDER BY "registeredAt" DESC,id DESC LIMIT :limit OFFSET :offset
    """), {'q':q.strip(),'status':status,'subject':subject,'group_name':group.strip(),'inactive_days':inactive_days,'limit':limit,'offset':offset})
    return [dict(r) for r in result.mappings()]

@router.get('', include_in_schema=False)
async def list_students(principal: AdminPrincipal, session: Session,
 q: str=Query('',max_length=120),status: Literal['active','blocked']|None=None,
 subject: Literal['history','society','both']|None=None,group: str=Query('',max_length=80),
 inactive_days: int=Query(0,ge=0,le=3650),offset: int=Query(0,ge=0),limit: int=Query(50,ge=1,le=100)):
    rows=await filtered_students(session,q,status,subject,group,inactive_days,offset,limit+1)
    return {'items':rows[:limit],'hasMore':len(rows)>limit,'total':rows[0]['total'] if rows else 0}

@router.get('/export.xlsx', include_in_schema=False)
async def export_students(principal: AdminPrincipal, session: Session,
 q: str=Query('',max_length=120),status: Literal['active','blocked']|None=None,
 subject: Literal['history','society','both']|None=None,group: str=Query('',max_length=80),
 inactive_days: int=Query(0,ge=0,le=3650)):
    from app.services.student_excel import student_workbook
    rows=await filtered_students(session,q,status,subject,group,inactive_days)
    ids=[str(r['id']) for r in rows]
    result=await session.execute(text("""
      SELECT a.user_id,u.display_name AS name,u.email,a.test_title_snapshot AS title,
        s.code AS subject,a.status::text AS status,a.started_at,a.completed_at,
        a.score,a.max_score,a.correct_count,a.question_count
      FROM app.test_attempts a JOIN app.users u ON u.id=a.user_id JOIN app.subjects s ON s.id=a.subject_id
      WHERE a.user_id=ANY(CAST(:ids AS uuid[])) ORDER BY a.started_at DESC
    """),{'ids':ids})
    return Response(student_workbook(rows,[dict(r) for r in result.mappings()]),
      media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers={'Content-Disposition':'attachment; filename="students-results.xlsx"'})

WEAK_QUERY = """
 SELECT s.code AS subject, coalesce(m.period,sec.title,'Без темы') AS topic,
   m.task_number AS "taskNumber",count(*) AS answered,
   count(*) FILTER (WHERE ans.is_correct=true) AS correct
 FROM app.test_attempts a JOIN app.attempt_questions aq ON aq.attempt_id=a.id
 JOIN app.attempt_answers ans ON ans.attempt_question_id=aq.id
 JOIN app.subjects s ON s.id=a.subject_id
 LEFT JOIN app.history_question_metadata m ON m.question_id=aq.question_id
 LEFT JOIN app.sections sec ON sec.id=a.section_id
 WHERE a.user_id=:id AND a.status='completed' AND ans.is_correct IS NOT NULL
 GROUP BY s.code,coalesce(m.period,sec.title,'Без темы'),m.task_number
 HAVING count(*)>=5
 ORDER BY count(*) FILTER (WHERE ans.is_correct=true)::numeric/count(*),count(*) DESC
"""


@router.get('/audit', include_in_schema=False)
async def admin_journal(principal: AdminPrincipal, session: Session,
 q: str=Query('',max_length=120),event: Literal['all','access','notes','reply','report_status','promotion','export','content','tests','media','other']='all',offset: int=Query(0,ge=0),limit: int=Query(50,ge=1,le=100)):
    result=await session.execute(text("""
      SELECT l.id,l.created_at AS "createdAt",l.action,l.entity_type AS "entityType",
       l.entity_id AS "entityId",l.new_data AS details,u.display_name AS name,u.email
      FROM app.audit_log l LEFT JOIN app.users u ON u.id=l.actor_user_id
      WHERE (:q='' OR strpos(lower(l.action),lower(:q))>0
        OR strpos(lower(coalesce(u.email,'')),lower(:q))>0
        OR strpos(lower(coalesce(u.display_name,'')),lower(:q))>0)
      AND (:event='all' OR CASE
        WHEN l.action LIKE '%/access' THEN 'access'
        WHEN l.action LIKE '%/notes' THEN 'notes'
        WHEN l.action LIKE '%/reply' THEN 'reply'
        WHEN l.action LIKE '%/question-error-reports/%/status' THEN 'report_status'
        WHEN l.action LIKE '%/export.xlsx' THEN 'export'
        WHEN l.action LIKE '%/admin/promotions%' THEN 'promotion'
        WHEN l.action LIKE '%/admin/media/%' THEN 'media'
        WHEN l.action LIKE '%/admin/questions%' OR l.action LIKE '%/admin/tests%' OR l.action LIKE '%/admin/history-trainer%' THEN 'tests'
        WHEN l.action LIKE '%/admin/content%' OR l.action LIKE '%/admin/reference%' THEN 'content'
        ELSE 'other' END=:event)
      ORDER BY l.created_at DESC,l.id DESC LIMIT :limit OFFSET :offset
    """),{'q':q.strip(),'event':event,'offset':offset,'limit':limit+1})
    rows=[dict(r) for r in result.mappings()]
    return {'items':rows[:limit],'hasMore':len(rows)>limit}


@router.get('/search-statistics', include_in_schema=False)
async def search_statistics(principal: AdminPrincipal,session: Session,start: date,end: date):
    if end<start or (end-start).days>36500:
        raise ProblemException(422,'invalid_period','Выберите корректный период')
    result=await session.execute(text("""
      WITH searches AS (
       SELECT visitor AS user_id,query,normalized_query,subject,category AS type
       FROM app.search_analytics
       WHERE NOT is_demo
        AND created_at>=(CAST(:start AS date)::timestamp AT TIME ZONE 'Europe/Moscow')
        AND created_at<((CAST(:end AS date)+1)::timestamp AT TIME ZONE 'Europe/Moscow')
      ), totals AS (
       SELECT subject,type,count(*) AS count,count(DISTINCT user_id) AS students
       FROM searches GROUP BY subject,type
      ), queries AS (
       SELECT subject,type,min(query) AS query,count(*) AS count,
        row_number() OVER(PARTITION BY subject,type ORDER BY count(*) DESC,min(query)) AS rank
       FROM searches GROUP BY subject,type,normalized_query
      ) SELECT t.subject,t.type,t.count,t.students,q.query,q.count AS "queryCount"
      FROM totals t LEFT JOIN queries q ON q.subject=t.subject AND q.type=t.type AND q.rank<=10
      ORDER BY t.subject,t.type,q.rank
    """),{'start':start,'end':end})
    categories={('history','date'):{'title':'Даты','count':0,'students':0,'queries':[]},
      ('history','term'):{'title':'Термины по истории','count':0,'students':0,'queries':[]},
      ('society','term'):{'title':'Термины по обществознанию','count':0,'students':0,'queries':[]},
      ('society','plan'):{'title':'Планы','count':0,'students':0,'queries':[]}}
    for row in result.mappings():
        category=categories.get((row['subject'],row['type']))
        if category is None:continue
        category['count']=row['count'];category['students']=row['students']
        if row['query']:category['queries'].append({'query':row['query'],'count':row['queryCount']})
    return {'items':list(categories.values()),'total':sum(c['count'] for c in categories.values())}

@router.get('/{student_id}/progress', include_in_schema=False)
async def admin_progress(student_id: UUID,principal: AdminPrincipal,session: Session,
 start: date,end: date,subject: Literal['history','society']|None=None):
    from app.services.student_timeline import timeline
    found=await session.execute(text("SELECT id FROM app.users WHERE id=:id AND role='student' AND status<>'deleted'"),{'id':student_id})
    if found.scalar_one_or_none() is None:
        raise ProblemException(404,'student_not_found','Ученик не найден')
    return await timeline(session,student_id,start,end,subject)

@router.get('/{student_id}', include_in_schema=False)
async def student_detail(student_id: UUID, principal: AdminPrincipal, session: Session):
    result = await session.execute(text(STUDENT_SELECT +
        " WHERE u.id=:id AND u.role='student' AND u.status<>'deleted'"), {'id': student_id})
    row = result.mappings().one_or_none()
    if row is None:
        raise ProblemException(404, 'student_not_found', 'Ученик не найден')
    searches = await session.execute(text("""
      SELECT h.query,h.created_at AS "createdAt",s.code AS subject,
             h.filters->>'type' AS type
      FROM app.search_history h LEFT JOIN app.subjects s ON s.id=h.subject_id
      WHERE h.user_id=:id ORDER BY h.created_at DESC,h.id DESC LIMIT 100
    """), {'id': student_id})
    weak=await session.execute(text(WEAK_QUERY), {'id':student_id})
    from app.services.mistake_review import review_summary
    review=await review_summary(session,student_id)
    return {'mistakeReview':review,'weakTopics':[dict(r) for r in weak.mappings()], 'student':dict(row), 'searches':[dict(r) for r in searches.mappings()]}


class StudentAccess(BaseModel):
    blocked: bool


@router.patch('/{student_id}/access', include_in_schema=False)
async def change_student_access(student_id: UUID, payload: StudentAccess,
                                principal: AdminPrincipal, session: Session):
    # Lock the account before revoking sessions; login locks this same user row.
    result = await session.execute(text("""
      SELECT id FROM app.users WHERE id=:id AND role='student' AND status<>'deleted' FOR UPDATE
    """), {'id':student_id})
    if result.scalar_one_or_none() is None:
        raise ProblemException(404, 'student_not_found', 'Ученик не найден')
    await session.execute(text("""
      UPDATE app.users SET status=CAST(:status AS app.account_status),updated_at=now() WHERE id=:id
    """), {'id':student_id,'status':'blocked' if payload.blocked else 'active'})
    if payload.blocked:
        await session.execute(text("UPDATE app.sessions SET revoked_at=now() WHERE user_id=:id AND revoked_at IS NULL"), {'id':student_id})
    await session.commit()
    return {'status':'blocked' if payload.blocked else 'active'}


class StudentNotes(BaseModel):
    notes: str=Field('',max_length=10000)
    groups: list[str]=Field(default_factory=list,max_length=20)
    subjects: list[Literal['history','society']]=Field(default_factory=list,max_length=2)

    @field_validator('groups')
    @classmethod
    def clean_groups(cls, values):
        values=list(dict.fromkeys(x.strip() for x in values if x.strip()))
        if any(len(x)>80 for x in values):
            raise ValueError('Название группы: не более 80 символов')
        return values

@router.put('/{student_id}/notes', include_in_schema=False)
async def save_notes(student_id: UUID,payload: StudentNotes,principal: AdminPrincipal,session: Session):
    found=await session.execute(text("SELECT id FROM app.users WHERE id=:id AND role='student' AND status<>'deleted' FOR UPDATE"),{'id':student_id})
    if found.scalar_one_or_none() is None:
        raise ProblemException(404,'student_not_found','Ученик не найден')
    await session.execute(text("""
      INSERT INTO app.student_admin_profiles(user_id,notes,groups,subjects,updated_by)
      VALUES (:id,:notes,CAST(:groups AS text[]),CAST(:subjects AS text[]),:admin)
      ON CONFLICT(user_id) DO UPDATE SET notes=excluded.notes,groups=excluded.groups,
        subjects=excluded.subjects,updated_at=now(),updated_by=excluded.updated_by
    """),{'id':student_id,'notes':payload.notes,'groups':payload.groups,
      'subjects':sorted(set(payload.subjects)),'admin':principal.user_id})
    await session.commit()
    return {'saved':True}
