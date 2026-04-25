from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from aion_terminal.agents.brief_agent import generate_morning_brief, post_brief_tags, save_brief_to_file
from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection

SCHEMA_PATH = "aion_terminal/storage/schema.sql"


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

    if not args.no_tags and not brief.error:
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)
        try:
            tags_posted = post_brief_tags(brief, conn)
        finally:
            conn.close()

    save_brief_to_file(brief)

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
    print()
    print("--- FULL BRIEF ---")
    print(brief.full_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
