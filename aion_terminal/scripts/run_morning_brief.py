from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from aion_terminal.agents.brief_agent import (
    generate_morning_brief,
    post_brief_tags,
    save_brief_to_db,
    save_brief_to_file,
)
from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection

SCHEMA_PATH = "aion_terminal/storage/schema.sql"
BRIEFS_DIR = Path("aion_terminal/data/briefs")


def write_weekly_rollup(conn, today: date, output_dir: Path = BRIEFS_DIR) -> str:
    """Write a single JSON file with this week's briefs (Mon..Fri)."""
    monday = today - timedelta(days=today.weekday())
    rows = conn.execute(
        """
        SELECT brief_id, brief_date, generated_at, regime, dominant_signal,
               regime_30d_call, risk_level, sector_leaders_json, sector_laggards_json,
               narrative_tags_json, full_text, exec_summary, model, tokens_used, raw_json
        FROM morning_briefs
        WHERE brief_date >= ? AND brief_date <= ?
        ORDER BY brief_date ASC
        """,
        (monday.isoformat(), today.isoformat()),
    ).fetchall()

    briefs: list[dict] = []
    for r in rows:
        try:
            raw = json.loads(r["raw_json"])
        except Exception:
            raw = {}
        briefs.append({
            "brief_date": r["brief_date"],
            "generated_at": r["generated_at"],
            "regime": r["regime"],
            "dominant_signal": r["dominant_signal"],
            "regime_30d_call": r["regime_30d_call"],
            "risk_level": r["risk_level"],
            "exec_summary": r["exec_summary"],
            "raw": raw,
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"week_{today.isoformat()}.json"
    path.write_text(
        json.dumps({"week_ending": today.isoformat(), "briefs": briefs}, indent=2),
        encoding="utf-8",
    )
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run morning macro brief generation")
    parser.add_argument("--no-tags", action="store_true", help="Skip writing narrative tags to SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Do not call API")
    args = parser.parse_args()

    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set. Add it to your environment or .env file.")
        return 1

    if args.dry_run:
        print("DRY RUN — API call skipped")
        return 0

    brief = generate_morning_brief(settings.watchlist, api_key=api_key)
    tags_posted = 0

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        if not brief.error:
            save_brief_to_db(conn, brief)
        if not args.no_tags and not brief.error:
            tags_posted = post_brief_tags(brief, conn)

        today = date.today()
        rollup_path = None
        if today.weekday() == 4:  # Friday
            rollup_path = write_weekly_rollup(conn, today)
    finally:
        conn.close()

    print("=== MORNING MACRO BRIEF ===")
    print(f"Generated: {brief.generated_at}")
    print(f"Model: {brief.model}")
    print(f"Tokens used: {brief.tokens_used}")
    print(f"Regime: {brief.regime} | Risk level: {brief.risk_level}")
    print(f"30-day call: {brief.regime_30d_call}")
    print()
    print(f"Sector leaders: {brief.sector_leaders}")
    print(f"Sector laggards: {brief.sector_laggards}")
    print()
    print(f"Narrative tags posted: {tags_posted}")
    if rollup_path:
        print(f"Weekly rollup written: {rollup_path}")
    print()
    print("--- FULL BRIEF ---")
    print(brief.full_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
