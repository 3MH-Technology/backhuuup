import secrets
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("wolfhost.email")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "HOST.FOR.WOLF@gmail.com"
SMTP_PASS = "nqfcippyciaucpbz"
FROM_NAME = "Wolf Host"


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def generate_verification_code() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


def send_verification_email(to_email: str, code: str) -> bool:
    html = f"""
    <div dir="rtl" style="font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 500px; margin: 0 auto; padding: 30px; background: #0a0f1e; border-radius: 16px; color: #e5e7eb;">
      <div style="text-align: center; margin-bottom: 24px;">
        <h1 style="color: #60a5fa; font-size: 24px; margin: 0;">🐺 Wolf Host</h1>
        <p style="color: #6b7280; font-size: 13px; margin: 4px 0 0;">استضافة الذب هوست</p>
      </div>
      <div style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 24px; text-align: center;">
        <p style="color: #9ca3af; font-size: 14px; margin: 0 0 16px;">رمز التحقق الخاص بك</p>
        <div style="background: #1e293b; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
          <span style="color: #f8fafc; font-size: 32px; font-weight: bold; letter-spacing: 8px; font-family: monospace;">{code}</span>
        </div>
        <p style="color: #6b7280; font-size: 12px; margin: 0;">صالح لمدة 15 دقيقة فقط</p>
      </div>
      <div style="text-align: center; margin-top: 20px;">
        <p style="color: #4b5563; font-size: 11px;">Developer: الذئب الأبيض 🐺 | @j49_c</p>
      </div>
    </div>
    """
    return _send_email(to_email, f"🐺 Wolf Host — رمز التحقق: {code}", html)


def send_welcome_email(to_email: str, username: str) -> bool:
    html = f"""
    <div dir="rtl" style="font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 500px; margin: 0 auto; padding: 30px; background: #0a0f1e; border-radius: 16px; color: #e5e7eb;">
      <div style="text-align: center; margin-bottom: 24px;">
        <h1 style="color: #60a5fa; font-size: 24px; margin: 0;">🐺 Wolf Host</h1>
      </div>
      <div style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 24px; text-align: center;">
        <p style="font-size: 18px; margin: 0 0 12px;">مرحباً {username}! 🎉</p>
        <p style="color: #9ca3af; font-size: 14px; margin: 0;">تم تسجيل حسابك بنجاح في Wolf Host</p>
        <p style="color: #9ca3af; font-size: 14px; margin: 12px 0 0;">يمكنك الآن إنشاء البوتات مجاناً 🚀</p>
      </div>
      <div style="text-align: center; margin-top: 20px;">
        <p style="color: #4b5563; font-size: 11px;">Developer: الذئب الأبيض 🐺 | @j49_c</p>
      </div>
    </div>
    """
    return _send_email(to_email, f"🐺 مرحباً {username} في Wolf Host!", html)
