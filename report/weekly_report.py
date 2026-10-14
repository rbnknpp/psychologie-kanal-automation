"""Erstellt und verschickt den woechentlichen Kanal-Bericht.

Aufruf: python weekly_report.py
"""
import datetime
import json

import config
from auth import get_credentials
from analytics import get_channel_overview, get_weekly_metrics, get_top_videos
from performance_insights import build_insights
from report import build_pdf
from email_sender import send_report


def run():
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=7)
    start_str, end_str = start_date.isoformat(), end_date.isoformat()

    print(f"Zeitraum: {start_str} bis {end_str}")

    channel_reports = []
    for ch in config.CHANNELS:
        print(f"Sammle Daten fuer: {ch['label']}...")
        creds = get_credentials(ch["token_file"])
        overview = get_channel_overview(creds)
        metrics = get_weekly_metrics(creds, start_str, end_str)
        top_videos = get_top_videos(creds, start_str, end_str)
        channel_reports.append({
            "label": ch["label"],
            "overview": overview,
            "metrics": metrics,
            "top_videos": top_videos,
        })

        insights = build_insights(creds, ch["pipeline_dir"])
        insights_path = ch["insights_file"]
        insights_path.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Erfolgsmuster aktualisiert: {len(insights['top'])} Top-, {len(insights['flop'])} Flop-Videos")

    pdf_path = config.REPORTS_DIR / f"bericht_{end_str}.pdf"
    build_pdf(channel_reports, start_str, end_str, str(pdf_path))
    print(f"PDF erstellt: {pdf_path}")

    total_views = sum(c["metrics"]["views"] for c in channel_reports)
    subject = f"YouTube-Wochenbericht {start_str} bis {end_str} ({total_views} Aufrufe gesamt)"
    body = "Anbei der automatische woechentliche Bericht fuer beide YouTube-Kanaele.\n\n" + "\n".join(
        f"- {c['label']}: {c['overview']['subscribers']} Abonnenten, "
        f"{c['metrics']['views']} Aufrufe diese Woche"
        for c in channel_reports
    )
    send_report(str(pdf_path), subject, body)
    print(f"E-Mail an {config.EMAIL_TO} verschickt.")


if __name__ == "__main__":
    run()
