from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.api.routes.student_dashboard import StudentMessage, create_message
from app.core.config import Settings
from app.core.problems import ProblemException
from app.main import app
from app.services import admin_notifications as notifications


class Result:
    def __init__(self, row=None, scalar=None):
        self.row, self.scalar = row, scalar

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.row or [])

    def one(self):
        return self.row

    def one_or_none(self):
        return self.row

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


def test_dashboard_requires_login():
    with TestClient(app) as client:
        for path in ('/api/v1/me/practice', '/api/v1/me/practice/attempts',
                     '/api/v1/admin/notification-settings'):
            assert client.get(path).status_code == 401
        assert client.post('/api/v1/me/messages', json={'message': 'Мой вопрос'}).status_code == 401


@pytest.mark.asyncio
async def test_message_and_email_queue_committed_together():
    report_id, user_id = uuid4(), uuid4()
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        Result(), Result(scalar=0),
        Result(row={'id': report_id, 'created_at': datetime.now(UTC)}), Result(),
    ]), commit=AsyncMock())
    tasks = BackgroundTasks()
    result = await create_message(StudentMessage(message='  Мой вопрос  '),
                                  SimpleNamespace(user_id=user_id), session, tasks)
    assert result['id'] == report_id
    session.commit.assert_awaited_once()
    assert session.execute.call_args_list[2].args[1]['message'] == 'Мой вопрос'
    assert session.execute.call_args_list[3].args[1]['id'] == report_id
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is notifications.deliver_admin_notifications


@pytest.mark.asyncio
async def test_rate_limit_does_not_create_message_or_mail():
    session = SimpleNamespace(execute=AsyncMock(side_effect=[Result(), Result(scalar=5)]),
                              commit=AsyncMock())
    tasks = BackgroundTasks()
    with pytest.raises(ProblemException) as error:
        await create_message(StudentMessage(message='Мой вопрос'),
                             SimpleNamespace(user_id=uuid4()), session, tasks)
    assert error.value.status == 429
    session.commit.assert_not_awaited()
    assert not tasks.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_mail_only_to_admin_and_failure_retryable(monkeypatch, fail):
    settings = Settings(jwt_secret='x' * 32, smtp_host='smtp.example.com',
                        smtp_from='sender@example.com', public_base_url='https://school.example.com')
    row = {'id': uuid4(), 'attempt_count': 0, 'message': 'Нужна помощь', 'question_id': None,
           'display_name': 'Ученик', 'student_email': 'student@example.com'}
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        Result(scalar='admin@example.com'), Result(row=row), Result(),
        Result(scalar='admin@example.com'), Result(row=None),
    ]))

    @asynccontextmanager
    async def begin():
        yield session

    monkeypatch.setattr(notifications, 'SessionFactory', SimpleNamespace(begin=begin))
    monkeypatch.setattr(notifications, 'get_settings', lambda: settings)
    sender = Mock(side_effect=RuntimeError('SMTP unavailable') if fail else None)
    monkeypatch.setattr(notifications, 'send_email', sender)
    await notifications.deliver_admin_notifications()
    message = sender.call_args.args[1]
    assert message['To'] == 'admin@example.com'
    assert message['To'] != row['student_email']
    assert '/admin#messages' in message.get_content()
    update = str(session.execute.call_args_list[2].args[0])
    assert ('next_attempt_at' if fail else 'sent_at=now()') in update
    if fail:
        assert session.execute.call_args_list[2].args[1]['delay'] == 60


@pytest.mark.asyncio
async def test_admin_reply_does_not_send_or_queue_email(monkeypatch):
    from app.api.routes.admin_practice import (
        QuestionErrorReportReply,
        admin_reply_question_error_report,
    )

    sender = Mock()
    monkeypatch.setattr(notifications, 'send_email', sender)
    report_id = uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(row={
        'id': report_id, 'status': 'answered', 'admin_reply': 'Исправили',
        'replied_at': datetime.now(UTC),
    })), commit=AsyncMock())
    response = await admin_reply_question_error_report(
        report_id, QuestionErrorReportReply(message='Исправили'),
        SimpleNamespace(user_id=uuid4()), session, uuid4(),
    )
    assert response['adminReply'] == 'Исправили'
    assert session.execute.await_count == 1
    sender.assert_not_called()


@pytest.mark.asyncio
async def test_progress_aggregates_only_owned_answers_and_keeps_self_check_separate():
    from app.api.routes.student_dashboard import get_practice_progress

    user_id = uuid4()
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        Result(row={'completed': 2, 'active': 1}),
        Result(row=[{'task_number': 1, 'period': 'XVIII век',
                     'answered': 3, 'correct': 2, 'self_check': 0},
                    {'task_number': 13, 'period': None,
                     'answered': 0, 'correct': 0, 'self_check': 1}]),
        Result(scalar=1),
    ]))
    data = await get_practice_progress(SimpleNamespace(user_id=user_id), session)
    assert data['summary'] == {'completed': 2, 'active': 1, 'answered': 3,
                               'correct': 2, 'selfCheck': 1}
    assert data['groups'][0]['taskNumber'] == 1
    assert data['mistakeCount'] == 1
    for call in session.execute.call_args_list:
        assert call.args[1]['user_id'] == user_id


@pytest.mark.asyncio
async def test_mark_read_is_scoped_to_message_owner():
    from app.api.routes.student_dashboard import mark_message_read

    user_id, report_id = uuid4(), uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(scalar=report_id)),
                              commit=AsyncMock())
    result = await mark_message_read(report_id, SimpleNamespace(user_id=user_id), session)
    assert result['read'] is True
    assert session.execute.call_args.args[1] == {'id': report_id, 'user_id': user_id}
    session.commit.assert_awaited_once()
    session.execute.return_value = Result(scalar=None)
    with pytest.raises(ProblemException) as error:
        await mark_message_read(uuid4(), SimpleNamespace(user_id=user_id), session)
    assert error.value.status == 404
