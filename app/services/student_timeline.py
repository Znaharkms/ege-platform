from datetime import date, timedelta
from sqlalchemy import text
from app.core.problems import ProblemException

DAILY_SQL = """
SELECT (a.completed_at AT TIME ZONE 'Europe/Moscow')::date AS day,
 count(*) AS completed,coalesce(sum(a.score),0) AS score,
 coalesce(sum(a.max_score),0) AS "maxScore",
 coalesce(sum(answers.checked),0) AS checked,coalesce(sum(answers.correct),0) AS correct
FROM app.test_attempts a JOIN app.subjects s ON s.id=a.subject_id
LEFT JOIN LATERAL (
 SELECT count(*) FILTER (WHERE ans.is_correct IS NOT NULL) AS checked,
 count(*) FILTER (WHERE ans.is_correct=true) AS correct
 FROM app.attempt_questions aq JOIN app.attempt_answers ans ON ans.attempt_question_id=aq.id
 WHERE aq.attempt_id=a.id
) answers ON true
WHERE a.user_id=:id AND a.status='completed'
 AND a.completed_at >= (CAST(:start AS date)::timestamp AT TIME ZONE 'Europe/Moscow')
 AND a.completed_at < ((CAST(:end AS date)+1)::timestamp AT TIME ZONE 'Europe/Moscow')
 AND (CAST(:subject AS text) IS NULL OR s.code=:subject)
GROUP BY day ORDER BY day
"""

async def timeline(session,user_id,start:date,end:date,subject=None):
    if end<start or (end-start).days>36500:
        raise ProblemException(422,'invalid_period','Выберите корректный период')
    length=(end-start).days+1
    previous_start=start-timedelta(days=length)
    result=await session.execute(text(DAILY_SQL),{'id':user_id,'start':previous_start,'end':end,'subject':subject})
    lookup={r['day']:dict(r) for r in result.mappings()}
    def period(first,last):
        days=[];day=first
        while day<=last:
            row=lookup.get(day,{'day':day,'completed':0,'score':0,'maxScore':0,'checked':0,'correct':0})
            days.append(row);day+=timedelta(days=1)
        totals={key:sum(r[key] for r in days) for key in ['completed','score','maxScore','checked','correct']}
        totals['scorePercent']=round(float(totals['score']/totals['maxScore'])*100,1) if totals['maxScore'] else None
        totals['accuracy']=round(float(totals['correct']/totals['checked'])*100,1) if totals['checked'] else None
        return {'start':first,'end':last,'days':days,'totals':totals}
    return {'current':period(start,end),'previous':period(previous_start,start-timedelta(days=1))}
