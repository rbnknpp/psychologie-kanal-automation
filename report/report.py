"""Baut den kompakten woechentlichen PDF-Bericht (ein Abschnitt pro Kanal)."""
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def build_pdf(channel_reports: list[dict], start_date: str, end_date: str, out_path: str) -> str:
    """channel_reports: Liste von {"label", "overview", "metrics", "top_videos"}."""
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=20)
    heading_style = ParagraphStyle("HeadingCustom", parent=styles["Heading2"], spaceBefore=16)
    normal = styles["Normal"]

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )

    story = [
        Paragraph("Wöchentlicher Kanal-Bericht", title_style),
        Paragraph(f"Zeitraum: {start_date} bis {end_date}", normal),
        Spacer(1, 0.5 * cm),
    ]

    for ch in channel_reports:
        story.append(Paragraph(ch["label"], heading_style))

        ov = ch["overview"]
        m = ch["metrics"]
        net_subs = m["subs_gained"] - m["subs_lost"]
        sign = "+" if net_subs >= 0 else ""

        data = [
            ["Kennzahl", "Wert"],
            ["Abonnenten aktuell", f"{ov['subscribers']:,}".replace(",", ".")],
            ["Abonnenten diese Woche", f"{sign}{net_subs} ({m['subs_gained']} neu, {m['subs_lost']} verloren)"],
            ["Aufrufe diese Woche", f"{m['views']:,}".replace(",", ".")],
            ["Wiedergabezeit diese Woche", f"{m['watch_hours']} Stunden"],
            ["Ø Zuschauerbindung", f"{m['avg_retention']}%"],
        ]
        table = Table(data, colWidths=[7 * cm, 8 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a1a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f5f5f5")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)
        story.append(Spacer(1, 0.3 * cm))

        if ch["top_videos"]:
            story.append(Paragraph("Top-Videos der Woche (Aufrufe / Zuschauerbindung):", styles["Heading4"]))
            for i, v in enumerate(ch["top_videos"], start=1):
                views_fmt = f"{v['views']:,}".replace(",", ".")
                story.append(Paragraph(f"{i}. {v['title']} — {views_fmt} Aufrufe / {v['retention']}% Bindung", normal))
        else:
            story.append(Paragraph("Keine Aufrufe in diesem Zeitraum.", normal))

        story.append(Spacer(1, 0.5 * cm))

    doc.build(story)
    return out_path
