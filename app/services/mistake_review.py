from sqlalchemy import text

REVIEW_SQL = """
WITH graded AS (
 SELECT aq.question_id,ans.is_correct,ans.answered_at,ans.id,
 row_number() OVER(PARTITION BY aq.question_id ORDER BY ans.answered_at DESC,ans.id DESC) AS rank,
 bool_or(NOT ans.is_correct) OVER(PARTITION BY aq.question_id) AS had_error
 FROM app.attempt_questions aq JOIN app.attempt_answers ans ON ans.attempt_question_id=aq.id
 JOIN app.test_attempts a ON a.id=aq.attempt_id JOIN app.tests t ON t.id=a.test_id
 WHERE a.user_id=:id AND t.slug='history-trainer' AND ans.is_correct IS NOT NULL
), latest AS (
 SELECT g.*,m.task_number,m.period FROM graded g
 JOIN app.questions q ON q.id=g.question_id AND q.status='published'
 JOIN app.history_question_metadata m ON m.question_id=q.id
 WHERE rank=1 AND had_error
)
SELECT task_number AS "taskNumber",period,
 count(*) FILTER(WHERE NOT is_correct) AS pending,
 count(*) FILTER(WHERE is_correct) AS corrected
FROM latest GROUP BY task_number,period ORDER BY task_number,period
"""

COMPARISON_SQL = """
SELECT count(*) AS compared,
 count(*) FILTER(WHERE prior.is_correct) AS "previousCorrect",
 count(*) FILTER(WHERE ans.is_correct) AS "currentCorrect",
 count(*) FILTER(WHERE NOT prior.is_correct AND ans.is_correct) AS corrected,
 count(*) FILTER(WHERE prior.is_correct AND NOT ans.is_correct) AS regressed
FROM app.test_attempts a JOIN app.attempt_questions aq ON aq.attempt_id=a.id
JOIN app.attempt_answers ans ON ans.attempt_question_id=aq.id
JOIN LATERAL (
 SELECT old.is_correct FROM app.attempt_answers old
 JOIN app.attempt_questions oq ON oq.id=old.attempt_question_id
 JOIN app.test_attempts oa ON oa.id=oq.attempt_id
 WHERE oa.user_id=a.user_id AND oq.question_id=aq.question_id AND oa.id<>a.id
 AND old.answered_at<a.started_at AND old.is_correct IS NOT NULL
 ORDER BY old.answered_at DESC,old.id DESC LIMIT 1
) prior ON true
WHERE a.id=:attempt AND a.user_id=:id AND a.status='completed' AND ans.is_correct IS NOT NULL
"""

async def review_summary(session,user_id):
 result=await session.execute(text(REVIEW_SQL),{'id':user_id})
 groups=[dict(r) for r in result.mappings()]
 return {'pending':sum(r['pending'] for r in groups),'corrected':sum(r['corrected'] for r in groups),'groups':groups}
