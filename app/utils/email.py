# Email utility for sending password-reset emails via SMTP.
# Uses Python's built-in smtplib + ssl — no extra packages needed.
# Connects on port 587 with STARTTLS (opportunistic encryption), which is
# the standard for Gmail / most SMTP providers.
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings


def send_otp_email(to_email: str, otp: str) -> None:
    """
    Send a password-reset OTP to `to_email`.

    Args:
        to_email:  Recipient email address.
        otp:       The 6-digit OTP code to send.
    """
    subject = "Your password reset code — Audio Intelligence Platform"

    plain_text = (
        f"Hi,\n\n"
        f"We received a request to reset your password.\n\n"
        f"Your one-time verification code is:\n\n"
        f"  {otp}\n\n"
        f"This code is valid for {settings.RESET_TOKEN_EXPIRE_MINUTES} minutes.\n\n"
        f"If you didn't request this, you can safely ignore this email.\n\n"
        f"— Audio Intelligence Platform"
    )

    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; background: #f3f4f6; padding: 32px;">
        <div style="max-width: 480px; margin: 0 auto; background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 6px rgba(0,0,0,0.07);">
          <h2 style="color: #111827; margin-bottom: 8px;">Password Reset</h2>
          <p style="color: #6b7280; margin-bottom: 24px;">
            We received a request to reset your password. Use the code below to continue.
          </p>
          <div style="text-align: center; margin: 32px 0;">
            <div style="display: inline-block; background: #f3f4f6; border-radius: 12px; padding: 20px 40px;">
              <span style="font-size: 36px; font-weight: 700; letter-spacing: 12px; color: #f97316; font-family: monospace;">
                {otp}
              </span>
            </div>
          </div>
          <p style="color: #6b7280; font-size: 14px; text-align: center;">
            This code expires in <strong>{settings.RESET_TOKEN_EXPIRE_MINUTES} minutes</strong>.
          </p>
          <p style="color: #9ca3af; font-size: 13px; text-align: center; margin-top: 24px;">
            If you did not request a password reset, you can safely ignore this email.
          </p>
          <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;" />
          <small style="color: #9ca3af;">Audio Intelligence Platform</small>
        </div>
      </body>
    </html>
    """

    # --- Assemble MIME message ---
    smtp_user = settings.SMTP_USER.strip()
    smtp_pass = settings.SMTP_PASSWORD.strip().replace(" ", "")
    smtp_from = (settings.SMTP_FROM or smtp_user).strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_from
    msg["To"]      = to_email

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    # --- Send via STARTTLS ---
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())

