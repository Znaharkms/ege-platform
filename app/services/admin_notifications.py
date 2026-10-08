from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage

from sqlalchemy import text

from app.core.config import get_settings
from app.core.email_delivery import send_email
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)


async def queue_admin_notification(session, report_id) -> None:
    await session.execute(text("""
        INSERT INTO app.admin_notification_outbox(report_id) VALUES (:id)
        ON CONFLICT (report_id) DO NOTHING
    """), {"id": report_id})


async def deliver_admin_notifications() -> None:
    try:
        await _deliver_pending_notifications()
    except Exception:
        # The report is already committed; a temporary queue error must not
        # make the student's successful submission look like a failure.
        logger.error('Очередь уведомлений временно недоступна')


async def _deliver_pending_notifications() -> None:
    settings = get_settings()
    if not settings.smtp_host:
        return
    for _ in range(20):
        async with SessionFactory.begin() as session:
            recipient = await session.execute(text(
                "SELECT email FROM app.admin_notification_settings WHERE singleton=true"
            ))
            email = recipient.scalar_one_or_none() or settings.admin_notification_email
            if not email:
                return
            result = await session.execute(text("""
                SELECT queue.id, queue.attempt_count, report.message, report.question_id,
                       reporter.display_name, reporter.email AS student_email
                FROM app.admin_notification_outbox AS queue
                JOIN app.question_error_reports AS report ON report.id=queue.report_id
                LEFT JOIN app.users AS reporter ON reporter.id=report.user_id
                WHERE queue.sent_at IS NULL AND queue.next_attempt_at <= now()
                ORDER BY queue.created_at LIMIT 1 FOR UPDATE OF queue SKIP LOCKED
            """))
            row = result.mappings().one_or_none()
            if row is None:
                return
            message = EmailMessage()
            message['To'] = email
            message['From'] = settings.smtp_from or settings.smtp_username
            message['Subject'] = 'Новое обращение ученика — ЕГЭ-онлайн Олеся Блок'
            name = row['display_name'] or row['student_email'] or 'Ученик без регистрации'
            context = (
                'Ошибка в вопросе тренажёра' if row['question_id'] else 'Обращение из кабинета'
            )
            message.set_content(
                f'{context}\nОт: {name}\n\n{row["message"]}\n\n'
                f'Открыть обращения: {settings.public_base_url.rstrip("/")}/admin#messages\n'
                'Ответьте в кабинете администратора. Ответ будет виден ученику только на платформе.'
            )
            try:
                await asyncio.to_thread(send_email, settings, message)
            except Exception:
                logger.error('Не удалось отправить уведомление администратору')
                await session.execute(text("""
                    UPDATE app.admin_notification_outbox
                    SET attempt_count=attempt_count+1, last_error='SMTP delivery failed',
                        next_attempt_at=now()+make_interval(secs => :delay)
                    WHERE id=:id
                """), {'id': row['id'], 'delay': min(3600, 60 * 2 ** min(row['attempt_count'], 6))})
            else:
                await session.execute(text("""
                    UPDATE app.admin_notification_outbox SET sent_at=now(),last_error=NULL,
                    attempt_count=attempt_count+1 WHERE id=:id
                """), {'id': row['id']})


async def notification_worker() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await deliver_admin_notifications()
        except Exception:
            logger.error('Очередь уведомлений временно недоступна')
