"""Verschickt den PDF-Bericht per E-Mail ueber Gmail SMTP (App-Passwort)."""
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config


def send_report(pdf_path: str, subject: str, body: str) -> None:
    if not config.EMAIL_APP_PASSWORD:
        raise RuntimeError(
            "EMAIL_APP_PASSWORD fehlt in .env. App-Passwort erstellen unter "
            "https://myaccount.google.com/apppasswords"
        )

    msg = MIMEMultipart()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    pdf_bytes = Path(pdf_path).read_bytes()
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header(
        "Content-Disposition", "attachment", filename=Path(pdf_path).name
    )
    msg.attach(attachment)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.EMAIL_FROM, config.EMAIL_APP_PASSWORD)
        server.send_message(msg)
