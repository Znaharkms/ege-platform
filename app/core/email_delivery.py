import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import Settings


def send_login_code(settings: Settings, email: str, code: str) -> None:
    message = EmailMessage()
    message['Subject'] = 'Код входа — ЕГЭ-онлайн Олеся Блок'
    message['From'] = settings.smtp_from or settings.smtp_username
    message['To'] = email
    message.set_content(
        f'Твой код входа: {code}\n\n'
        f'Код действует {settings.email_code_ttl_minutes} минут.\n'
        'Если ты не запрашивал вход, проигнорируй это письмо.'
    )
    send_email(settings, message)


def send_email(settings: Settings, message: EmailMessage) -> None:
    transport = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
    options = {"context": ssl.create_default_context()} if settings.smtp_ssl else {}
    with transport(settings.smtp_host, settings.smtp_port, timeout=15, **options) as server:
        if not settings.smtp_ssl:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        server.send_message(message)
