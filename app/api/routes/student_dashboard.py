from __future__ import annotations
from datetime import date
from typing import Literal

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.api.dependencies import AdminPrincipal, CurrentPrincipal
from app.api.routes.catalog import Session, _offset, _page
from app.core.config import get_settings
from app.core.problems import ProblemException
from app.repositories.practice import ATTEMPT_SELECT, attempt_summary
from app.services.admin_notifications import deliver_admin_notifications, queue_admin_notification
from app.services.student_progress import MISTAKES_QUERY

router = APIRouter(tags=['Student dashboard'])


class StudentMessage(BaseModel):
    message: str = Field(min_length=5, max_length=2000)


class NotificationSettings(BaseModel):
    email: EmailStr


@router.post('/me/messages', status_code=201, include_in_schema=False)
async def create_message(payload: StudentMessage, principal: CurrentPrincipal,
                         session: Session, background_tasks: BackgroundTasks):
    message = payload.message.strip()
    if len(message) < 5:
        raise ProblemException(422, 'message_too_short', 'Опишите вопрос подробнее')
    # Serialize requests per account so the limit also holds for simultaneous submits.
    await session.execute(text('SELECT id FROM app.users WHERE id=:id FOR UPDATE'),
                          {'id': principal.user_id})
    recent = await session.execute(text("""
        SELECT count(*) FROM app.question_error_reports
        WHERE user_id=:id AND created_at > now()-interval '10 minutes'
    """), {'id': principal.user_id})
    if recent.scalar_one() >= 5:
        raise ProblemException(429, 'message_rate_limited', 'Повторите попытку позднее')
    result = await session.execute(text("""
        INSERT INTO app.question_error_reports(user_id,message) VALUES (:id,:message)
        RETURNING id,created_at
    """), {'id': principal.user_id, 'message': message})
    report = result.mappings().one()
    await queue_admin_notification(session, report['id'])
    await session.commit()
    background_tasks.add_task(deliver_admin_notifications)
    return {'id': report['id'], 'createdAt': report['created_at'], 'status': 'open'}


@router.get('/admin/notification-settings', include_in_schema=False)
async def get_notification_settings(admin: AdminPrincipal, session: Session):
    result = await session.execute(text(
        'SELECT email FROM app.admin_notification_settings WHERE singleton=true'
    ))
    email = result.scalar_one_or_none() or get_settings().admin_notification_email
    pending = await session.execute(text(
        'SELECT count(*) FROM app.admin_notification_outbox WHERE sent_at IS NULL'
    ))
    return {'email': email, 'smtpConfigured': bool(get_settings().smtp_host),
            'pendingCount': pending.scalar_one()}


@router.put('/admin/notification-settings', include_in_schema=False)
async def set_notification_settings(payload: NotificationSettings, admin: AdminPrincipal,
                                    session: Session, background_tasks: BackgroundTasks):
    await session.execute(text("""
        INSERT INTO app.admin_notification_settings(singleton,email) VALUES (true,:email)
        ON CONFLICT (singleton) DO UPDATE SET email=EXCLUDED.email
    """), {'email': str(payload.email)})
    await session.commit()
    background_tasks.add_task(deliver_admin_notifications)
    return {'email': str(payload.email)}


@router.get('/me/practice', include_in_schema=False)
async def get_practice_progress(principal: CurrentPrincipal, session: Session):
    parameters = {'user_id': principal.user_id}
    result = await session.execute(text("""
        SELECT count(*) FILTER (WHERE attempt.status='completed')::integer AS completed,
               count(*) FILTER (WHERE attempt.status='in_progress')::integer AS active
        FROM app.test_attempts AS attempt JOIN app.tests AS test ON test.id=attempt.test_id
        WHERE attempt.user_id=:user_id AND test.slug='history-trainer'
    """), parameters)
    totals = dict(result.mappings().one())
    result = await session.execute(text("""
        SELECT metadata.task_number,metadata.period,
               count(*) FILTER (WHERE answer.is_correct IS NOT NULL)::integer AS answered,
               count(*) FILTER (WHERE answer.is_correct=true)::integer AS correct,
               count(*) FILTER (WHERE answer.is_correct IS NULL)::integer AS self_check
        FROM app.test_attempts AS attempt
        JOIN app.tests AS test ON test.id=attempt.test_id
        JOIN app.attempt_questions AS aq ON aq.attempt_id=attempt.id
        JOIN app.attempt_answers AS answer ON answer.attempt_question_id=aq.id
        JOIN app.history_question_metadata AS metadata ON metadata.question_id=aq.question_id
        WHERE attempt.user_id=:user_id AND attempt.status='completed'
          AND test.slug='history-trainer'
        GROUP BY metadata.task_number,metadata.period
        ORDER BY metadata.task_number,metadata.period
    """), parameters)
    groups = [dict(row) for row in result.mappings()]
    totals.update(answered=sum(row['answered'] for row in groups),
                  correct=sum(row['correct'] for row in groups),
                  selfCheck=sum(row['self_check'] for row in groups))
    mistakes = await session.execute(text(f"""
        SELECT count(*)::integer FROM app.questions AS question
        WHERE question.status='published' AND question.id IN ({MISTAKES_QUERY})
    """), parameters)
    return {'summary': totals, 'groups': [
        {'taskNumber': row['task_number'], 'period': row['period'],
         'answered': row['answered'], 'correct': row['correct'], 'selfCheck': row['self_check']}
        for row in groups
    ], 'mistakeCount': mistakes.scalar_one()}


@router.get('/me/practice/attempts', include_in_schema=False)
async def get_my_attempts(principal: CurrentPrincipal, session: Session,
                          cursor: Annotated[str | None, Query(max_length=500)] = None,
                          limit: Annotated[int, Query(ge=1, le=100)] = 20):
    offset = _offset(cursor)
    result = await session.execute(text(f"""
        {ATTEMPT_SELECT}
        JOIN app.tests AS test ON test.id=attempt.test_id
        WHERE attempt.user_id=:user_id AND test.slug='history-trainer'
        ORDER BY attempt.started_at DESC,attempt.id DESC
        LIMIT :fetch_limit OFFSET :offset
    """), {'user_id': principal.user_id, 'fetch_limit': limit+1, 'offset': offset})
    rows = list(result.mappings())
    return {'items': [attempt_summary(row) for row in rows[:limit]],
            'page': _page(offset, limit, len(rows) > limit)}


@router.post('/me/messages/{report_id}/read', include_in_schema=False)
async def mark_message_read(report_id: UUID, principal: CurrentPrincipal, session: Session):
    result = await session.execute(text("""
        UPDATE app.question_error_reports
        SET student_viewed_at=COALESCE(student_viewed_at,now()),updated_at=now()
        WHERE id=:id AND user_id=:user_id AND admin_reply IS NOT NULL
        RETURNING id
    """), {'id': report_id, 'user_id': principal.user_id})
    if result.scalar_one_or_none() is None:
        raise ProblemException(404, 'message_not_found', 'Сообщение не найдено')
    await session.commit()
    return {'id': report_id, 'read': True}


@router.post('/me/activity', status_code=204, include_in_schema=False)
async def record_activity(principal: CurrentPrincipal, session: Session):
    await session.execute(text("UPDATE app.sessions SET last_used_at=now() WHERE id=:id"),
                          {"id": principal.session_id})
    await session.commit()


@router.get('/me/progress', include_in_schema=False)
async def student_progress(principal: CurrentPrincipal, session: Session,
                          start: date, end: date, subject: Literal['history','society']|None=None):
    from app.services.student_timeline import timeline
    return await timeline(session,principal.user_id,start,end,subject)


@router.get('/me/practice/mistakes',include_in_schema=False)
async def get_mistakes(principal:CurrentPrincipal,session:Session):
    from app.services.mistake_review import review_summary
    from app.api.routes.admin_students import WEAK_QUERY
    review=await review_summary(session,principal.user_id)
    result=await session.execute(text(WEAK_QUERY),{'id':principal.user_id})
    review['weakTopics']=[dict(r) for r in result.mappings()]
    return review


@router.get('/me/practice/comparison',include_in_schema=False)
async def compare_answers(principal:CurrentPrincipal,session:Session,attemptId:UUID):
    from app.services.mistake_review import COMPARISON_SQL
    owned=await session.execute(text("SELECT id FROM app.test_attempts WHERE id=:attempt AND user_id=:id AND status='completed'"),{'attempt':attemptId,'id':principal.user_id})
    if owned.scalar_one_or_none() is None:
        raise ProblemException(404,'attempt_not_found','Завершённая тренировка не найдена')
    result=await session.execute(text(COMPARISON_SQL),{'attempt':attemptId,'id':principal.user_id})
    return dict(result.mappings().one())
