import logging
import os
import random
import smtplib
from email.mime.text import MIMEText

from config import settings

logger = logging.getLogger("wolfhost.email")


def generate_code() -> str:
    return str(random.randint(100000, 999999))


def _get_smtp_credential(key: str) -> str:
    val = getattr(settings, key, "")
    if not val:
        env_key = key.upper()
        val = os.environ.get(env_key, "")
    return val


def send_verification(recipient: str, code: str) -> bool:
    smtp_user = _get_smtp_credential("smtp_user")
    smtp_password = _get_smtp_credential("smtp_password")
    if not smtp_user or not smtp_password:
        logger.warning("SMTP not configured — skipping email send")
        return False

    body = f"""
    <div dir="rtl" style="font-family: 'Tajawal', sans-serif; max-width: 500px; margin: 0 auto; padding: 30px; background: linear-gradient(135deg, #0a0f1e, #111827); border-radius: 16px;">
        <div style="text-align: center; margin-bottom: 25px;">
            <h1 style="color: #60a5fa; margin: 0; font-size: 24px;">🐺 Wolf Host</h1>
            <p style="color: #9ca3af; margin: 5px 0 0;">استضافة الذب هوست</p>
        </div>
        <div style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 25px; text-align: center;">
            <p style="color: #d1d5db; font-size: 16px; margin: 0 0 15px;">كود التحقق الخاص بك</p>
            <div style="background: rgba(37,99,235,0.15); border: 1px solid rgba(37,99,235,0.3); border-radius: 10px; padding: 15px; margin: 10px 0;">
                <span style="font-size: 36px; font-weight: 900; color: #60a5fa; letter-spacing: 8px;">{code}</span>
            </div>
            <p style="color: #6b7280; font-size: 13px; margin: 15px 0 0;">الكود صالح لمدة 10 دقائق. إذا لم تطلب هذا، تجاهل الرسالة.</p>
        </div>
        <p style="color: #4b5563; font-size: 11px; text-align: center; margin-top: 20px;">Wolf Host — Developer: الذئب الأبيض 🐺 | @j49_c | Support: @Wolfhost_1</p>
    </div>
    """

    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = "🐺 Wolf Host — كود التحقق"
    msg["From"] = smtp_user
    msg["To"] = recipient

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info(f"Verification email sent to {recipient}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {recipient}: {e}")
        return False
