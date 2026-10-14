"""Loescht lokale Lauf-Ordner in output/, die aelter als config.OUTPUT_RETENTION_DAYS
sind UND bei denen alle Videos bereits erfolgreich hochgeladen wurden (verhindert
Loeschen von etwas, das noch gebraucht wird).

Aufruf: python cleanup.py
"""
import datetime
import json
import shutil
import time

import config


def cleanup_old_outputs() -> None:
    cutoff = datetime.date.today() - datetime.timedelta(days=config.OUTPUT_RETENTION_DAYS)

    for run_dir in sorted(config.OUTPUT_DIR.iterdir()):
        if not run_dir.is_dir():
            continue
        try:
            run_date = datetime.date.fromisoformat(run_dir.name)
        except ValueError:
            continue
        if run_date >= cutoff:
            continue

        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"  Ueberspringe {run_dir.name}: kein manifest.json")
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest or not all(item.get("uploaded") for item in manifest):
            print(f"  Ueberspringe {run_dir.name}: nicht alle Videos hochgeladen")
            continue

        shutil.rmtree(run_dir)
        print(f"  Geloescht: {run_dir.name} (aelter als {config.OUTPUT_RETENTION_DAYS} Tage, komplett hochgeladen)")


def cleanup_stock_cache(max_age_days: int = 3) -> None:
    """Loescht heruntergeladene Stock-Clips, die seit mehreren Tagen nicht mehr
    gebraucht wurden - mit mehreren Clips pro Video waechst der Cache sonst schnell."""
    cutoff = time.time() - max_age_days * 86400
    freed = 0
    for f in config.STOCK_CACHE_DIR.glob("*.*"):
        if f.stat().st_mtime < cutoff:
            freed += f.stat().st_size
            f.unlink()
    print(f"  Stock-Cache aufgeraeumt: {freed / 1e9:.2f} GB freigegeben")


if __name__ == "__main__":
    cleanup_old_outputs()
    cleanup_stock_cache()
