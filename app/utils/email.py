# Email utility for sending password-reset emails via SMTP.
# Uses Python's built-in smtplib + ssl — no extra packages needed.
# Connects on port 587 with STARTTLS (opportunistic encryption), which is
# the standard for Gmail / most SMTP providers.
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings


def send_reset_email(to_email: str, raw_token: str) -> None:
    """
    Send a password-reset email to `to_email`.

    The link contains the raw token as a URL query parameter.
    The token has already been validated and stored (hashed) in the DB
    before this function is called.
    #DONE CHE
    Args:
        to_email:   Recipient email address.
        raw_token:  The un-hashed one-time reset token (URL-safe string).
    """
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={raw_token}"

    # --- Build HTML + plain-text body ---
    subject = "Reset your password — Audio Intelligence Platform"

    plain_text = (
        f"Hi,\n\n"
        f"We received a request to reset your password.\n\n"
        f"Click the link below to choose a new password (valid for {settings.RESET_TOKEN_EXPIRE_MINUTES} minutes):\n\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email.\n\n"
        f"— Audio Intelligence Platform"
    )

    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Password Reset Request</h2>
        <p>We received a request to reset the password for your account.</p>
        <p>
          <a href="{reset_url}"
             style="display:inline-block;padding:10px 20px;background:#4F46E5;
                    color:#fff;text-decoration:none;border-radius:6px;">
            Reset Password
          </a>
        </p>
        <p>This link expires in <strong>{settings.RESET_TOKEN_EXPIRE_MINUTES} minutes</strong>.</p>
        <p>If you did not request a password reset, you can safely ignore this email.</p>
        <hr/>
        <small>Audio Intelligence Platform</small>
      </body>
    </html>
    """

    # --- Assemble MIME message ---
    # Strip whitespace — Gmail App Passwords are sometimes copied with spaces
    smtp_user = settings.SMTP_USER.strip()
    smtp_pass = settings.SMTP_PASSWORD.strip().replace(" ", "")
    smtp_from = (settings.SMTP_FROM or smtp_user).strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = to_email

    # Attach plain-text first — email clients fall back to it if HTML is unsupported
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    # --- Send via STARTTLS ---
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())
