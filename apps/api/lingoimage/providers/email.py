"""Email delivery with localised, responsive templates.

Providers: SMTP (default), Resend, and a console sink for development.
Templates render both HTML and plain text, and never embed sensitive data —
links carry short-lived one-time tokens instead.
"""

from __future__ import annotations

import html
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx

from lingoimage.core.config import settings
from lingoimage.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class RenderedEmail:
    subject: str
    html_body: str
    text_body: str


#: subject / heading / body lines, per template per locale.
TEMPLATES: dict[str, dict[str, dict[str, Any]]] = {
    "verify_email": {
        "en": {
            "subject": "Confirm your email address",
            "heading": "Confirm your email",
            "body": [
                "Thanks for creating an account. Confirm your address to activate it.",
            ],
            "cta": "Confirm email",
            "footer": "This link expires in 24 hours. If you did not sign up, ignore this email.",
        },
        "ru": {
            "subject": "Подтвердите адрес электронной почты",
            "heading": "Подтвердите почту",
            "body": ["Спасибо за регистрацию. Подтвердите адрес, чтобы активировать аккаунт."],
            "cta": "Подтвердить почту",
            "footer": (
                "Ссылка действует 24 часа. Если вы не регистрировались, проигнорируйте письмо."
            ),
        },
    },
    "magic_link": {
        "en": {
            "subject": "Your sign-in link",
            "heading": "Sign in",
            "body": ["Use the button below to sign in. The link works once."],
            "cta": "Sign in",
            "footer": "This link expires in 15 minutes.",
        },
        "ru": {
            "subject": "Ссылка для входа",
            "heading": "Вход в аккаунт",
            "body": ["Нажмите кнопку ниже, чтобы войти. Ссылка одноразовая."],
            "cta": "Войти",
            "footer": "Ссылка действует 15 минут.",
        },
    },
    "password_reset": {
        "en": {
            "subject": "Reset your password",
            "heading": "Reset your password",
            "body": ["Choose a new password using the link below."],
            "cta": "Set a new password",
            "footer": "This link expires in 1 hour. If you did not request it, ignore this email.",
        },
        "ru": {
            "subject": "Сброс пароля",
            "heading": "Сброс пароля",
            "body": ["Задайте новый пароль по ссылке ниже."],
            "cta": "Задать новый пароль",
            "footer": "Ссылка действует 1 час. Если вы её не запрашивали, проигнорируйте письмо.",
        },
    },
    "job_completed": {
        "en": {
            "subject": "Your file is ready",
            "heading": "Processing finished",
            "body": ["Your document has been processed and is ready to download."],
            "cta": "Open result",
            "footer": "Files are removed automatically according to your retention settings.",
        },
        "ru": {
            "subject": "Файл готов",
            "heading": "Обработка завершена",
            "body": ["Документ обработан и готов к скачиванию."],
            "cta": "Открыть результат",
            "footer": "Файлы удаляются автоматически согласно настройкам хранения.",
        },
    },
    "job_failed": {
        "en": {
            "subject": "We could not process your file",
            "heading": "Processing failed",
            "body": ["Something went wrong. Any credits used have been returned."],
            "cta": "Try again",
            "footer": "Reply to this email with the request ID if the problem repeats.",
        },
        "ru": {
            "subject": "Не удалось обработать файл",
            "heading": "Ошибка обработки",
            "body": ["Что-то пошло не так. Списанные кредиты возвращены."],
            "cta": "Повторить",
            "footer": "Если ошибка повторяется, ответьте на письмо и укажите request ID.",
        },
    },
    "batch_completed": {
        "en": {
            "subject": "Your batch is ready",
            "heading": "Batch finished",
            "body": ["All files in your batch have been processed."],
            "cta": "Download results",
            "footer": "The archive stays available for your retention period.",
        },
        "ru": {
            "subject": "Пакетная обработка завершена",
            "heading": "Пакет готов",
            "body": ["Все файлы пакета обработаны."],
            "cta": "Скачать результаты",
            "footer": "Архив доступен в течение срока хранения.",
        },
    },
    "workspace_invitation": {
        "en": {
            "subject": "You have been invited to a workspace",
            "heading": "Join the workspace",
            "body": ["You were invited to collaborate."],
            "cta": "Accept invitation",
            "footer": "This invitation expires in 7 days.",
        },
        "ru": {
            "subject": "Приглашение в рабочее пространство",
            "heading": "Присоединиться к команде",
            "body": ["Вас пригласили к совместной работе."],
            "cta": "Принять приглашение",
            "footer": "Приглашение действует 7 дней.",
        },
    },
    "payment_succeeded": {
        "en": {
            "subject": "Payment received",
            "heading": "Thank you",
            "body": ["Your payment was processed successfully."],
            "cta": "View invoice",
            "footer": "",
        },
        "ru": {
            "subject": "Платёж получен",
            "heading": "Спасибо",
            "body": ["Платёж успешно обработан."],
            "cta": "Открыть счёт",
            "footer": "",
        },
    },
    "payment_failed": {
        "en": {
            "subject": "Payment failed",
            "heading": "We could not take payment",
            "body": ["Update your payment method to keep your plan active."],
            "cta": "Update payment method",
            "footer": "Your plan stays active during the grace period.",
        },
        "ru": {
            "subject": "Платёж не прошёл",
            "heading": "Не удалось списать оплату",
            "body": ["Обновите способ оплаты, чтобы сохранить тариф."],
            "cta": "Обновить оплату",
            "footer": "Тариф остаётся активным в течение льготного периода.",
        },
    },
    "subscription_cancelled": {
        "en": {
            "subject": "Your subscription was cancelled",
            "heading": "Subscription cancelled",
            "body": ["Your plan stays active until the end of the current period."],
            "cta": "Reactivate",
            "footer": "",
        },
        "ru": {
            "subject": "Подписка отменена",
            "heading": "Подписка отменена",
            "body": ["Тариф действует до конца оплаченного периода."],
            "cta": "Возобновить",
            "footer": "",
        },
    },
    "credits_low": {
        "en": {
            "subject": "Your credits are running low",
            "heading": "Low credit balance",
            "body": ["You have fewer credits left than a typical job needs."],
            "cta": "Top up",
            "footer": "",
        },
        "ru": {
            "subject": "Кредиты заканчиваются",
            "heading": "Мало кредитов",
            "body": ["Оставшихся кредитов может не хватить на следующую обработку."],
            "cta": "Пополнить",
            "footer": "",
        },
    },
    "security_alert": {
        "en": {
            "subject": "Security alert",
            "heading": "New sign-in detected",
            "body": ["A new device signed in to your account."],
            "cta": "Review sessions",
            "footer": "If this was not you, change your password immediately.",
        },
        "ru": {
            "subject": "Оповещение безопасности",
            "heading": "Новый вход в аккаунт",
            "body": ["В аккаунт вошли с нового устройства."],
            "cta": "Проверить сеансы",
            "footer": "Если это были не вы, немедленно смените пароль.",
        },
    },
    "data_deleted": {
        "en": {
            "subject": "Your data has been deleted",
            "heading": "Deletion complete",
            "body": ["Your account and files have been removed from our systems."],
            "cta": "",
            "footer": "Backups roll off within 30 days.",
        },
        "ru": {
            "subject": "Данные удалены",
            "heading": "Удаление завершено",
            "body": ["Аккаунт и файлы удалены из наших систем."],
            "cta": "",
            "footer": "Резервные копии удаляются в течение 30 дней.",
        },
    },
}


def render(template: str, locale: str, context: dict[str, Any]) -> RenderedEmail:
    localized = TEMPLATES.get(template, {})
    strings = localized.get(locale) or localized.get("en")
    if strings is None:
        strings = {"subject": template, "heading": template, "body": [], "cta": "", "footer": ""}

    brand = settings.brand_name
    subject = f"{strings['subject']} · {brand}"
    action_url = str(context.get("action_url") or "")
    extra_lines = [str(line) for line in context.get("lines", [])]
    body_lines = [*strings.get("body", []), *extra_lines]

    text_parts = [strings["heading"], "", *body_lines]
    if action_url:
        text_parts += ["", f"{strings.get('cta') or 'Open'}: {action_url}"]
    if strings.get("footer"):
        text_parts += ["", strings["footer"]]
    text_parts += ["", f"— {brand}"]

    escaped = [html.escape(line) for line in body_lines]
    cta_label = html.escape(strings.get("cta") or "Open")
    button = (
        f'<a href="{html.escape(action_url)}" class="btn">{cta_label}</a>'
        if action_url and strings.get("cta")
        else ""
    )
    html_body = f"""<!doctype html>
<html lang="{html.escape(locale)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(strings["subject"])}</title>
<style>
  body{{margin:0;background:#f6f7f9;color:#16181d;
        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}}
  .wrap{{max-width:560px;margin:0 auto;padding:32px 20px}}
  .card{{background:#fff;border-radius:14px;padding:32px;border:1px solid #e6e8ec}}
  h1{{font-size:20px;margin:0 0 16px}}
  p{{font-size:15px;line-height:1.6;margin:0 0 14px;color:#3a3f4a}}
  .btn{{display:inline-block;margin:12px 0;padding:12px 22px;
        background:#1f6feb;color:#fff !important;
        text-decoration:none;border-radius:8px;font-weight:600;font-size:15px}}
  .foot{{font-size:12px;color:#767c8a;margin-top:22px}}
  .brand{{font-size:13px;color:#767c8a;text-align:center;margin-top:18px}}
</style></head>
<body><div class="wrap"><div class="card">
<h1>{html.escape(strings["heading"])}</h1>
{"".join(f"<p>{line}</p>" for line in escaped)}
{button}
<p class="foot">{html.escape(strings.get("footer", ""))}</p>
</div><p class="brand">{html.escape(brand)}</p></div></body></html>"""

    return RenderedEmail(subject=subject, html_body=html_body, text_body="\n".join(text_parts))


def send(*, template: str, to: str, context: dict[str, Any]) -> dict[str, Any]:
    locale = str(context.get("locale") or settings.default_locale)
    message = render(template, locale, context)

    if settings.email_provider == "console":
        log.info("email.console", template=template, subject=message.subject)
        return {"delivered": True, "provider": "console"}
    if settings.email_provider == "resend":
        return _send_resend(to, message)
    return _send_smtp(to, message)


def _send_smtp(to: str, message: RenderedEmail) -> dict[str, Any]:
    email = EmailMessage()
    email["Subject"] = message.subject
    email["From"] = f"{settings.email_from_name} <{settings.email_from}>"
    email["To"] = to
    email.set_content(message.text_body)
    email.add_alternative(message.html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(email)
    except Exception as exc:
        log.error("email.smtp_failed", error=type(exc).__name__)
        return {"delivered": False, "provider": "smtp", "error": type(exc).__name__}
    return {"delivered": True, "provider": "smtp"}


def _send_resend(to: str, message: RenderedEmail) -> dict[str, Any]:
    if not settings.resend_api_key:
        log.error("email.resend_not_configured")
        return {"delivered": False, "provider": "resend", "error": "missing_api_key"}
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": f"{settings.email_from_name} <{settings.email_from}>",
                "to": [to],
                "subject": message.subject,
                "html": message.html_body,
                "text": message.text_body,
            },
            timeout=20,
        )
        response.raise_for_status()
        return {"delivered": True, "provider": "resend", "id": response.json().get("id")}
    except Exception as exc:
        log.error("email.resend_failed", error=type(exc).__name__)
        return {"delivered": False, "provider": "resend", "error": type(exc).__name__}


def health() -> dict[str, Any]:
    if settings.email_provider == "console":
        return {"state": "healthy", "detail": "console sink (development only)"}
    if settings.email_provider == "resend":
        return {
            "state": "healthy" if settings.resend_api_key else "not_configured",
            "detail": "RESEND_API_KEY" if not settings.resend_api_key else "",
        }
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5):
            return {"state": "healthy", "detail": f"{settings.smtp_host}:{settings.smtp_port}"}
    except Exception as exc:
        return {"state": "unavailable", "detail": type(exc).__name__}
