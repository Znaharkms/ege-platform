from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.core.email_delivery import send_login_code


def test_login_email_uses_tls_and_keeps_code_out_of_subject():
    settings = Settings(
        jwt_secret='x' * 32,
        smtp_host='smtp.example.com', smtp_port=587, smtp_ssl=False,
        smtp_username='sender@example.com', smtp_password='secret',
        email_code_ttl_minutes=7,
    )
    connection = MagicMock()
    with patch('app.core.email_delivery.smtplib.SMTP') as smtp:
        smtp.return_value.__enter__.return_value = connection
        send_login_code(settings, 'student@example.com', '123456')
    connection.starttls.assert_called_once()
    connection.login.assert_called_once_with('sender@example.com', 'secret')
    message = connection.send_message.call_args.args[0]
    assert message['To'] == 'student@example.com'
    assert message['From'] == 'sender@example.com'
    assert '123456' not in message['Subject']
    assert '123456' in message.get_content()
    assert '7 минут' in message.get_content()


def test_login_email_implicit_tls_does_not_start_tls_again():
    settings = Settings(jwt_secret='x' * 32, smtp_host='smtp.example.com',
                        smtp_from='sender@example.com')
    connection = MagicMock()
    with patch('app.core.email_delivery.smtplib.SMTP_SSL') as smtp:
        smtp.return_value.__enter__.return_value = connection
        send_login_code(settings, 'student@example.com', '123456')
    connection.starttls.assert_not_called()
    connection.send_message.assert_called_once()
