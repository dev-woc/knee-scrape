"""CLI: search, status, export commands."""

import argparse
import asyncio
import sys

from thomasnet_scraper.config import DEFAULT_DB_PATH
from thomasnet_scraper.utils import setup_logging


def _cmd_search(args: argparse.Namespace) -> None:
    from thomasnet_scraper.search_scraper import scrape_search

    asyncio.run(
        scrape_search(
            args.keyword,
            heading=args.heading,
            max_pages=args.max_pages,
            headless=not args.no_headless,
            db_path=args.db,
        )
    )


def _cmd_status(args: argparse.Namespace) -> None:
    from thomasnet_scraper.db import Database

    db = Database(args.db)
    sessions = db.get_all_sessions()
    if not sessions:
        print("No scrape sessions found.")
        db.close()
        return

    for s in sessions:
        stats = db.get_page_stats(s["id"])
        success = stats.get("success", 0)
        failed = stats.get("failed", 0)
        pending = stats.get("pending", 0)
        total = s["total_pages"] or "?"
        supplier_count = db.count_suppliers(s["search_term"])
        print(
            f"  Session {s['id']:>3}  │  '{s['search_term']}'  │  "
            f"status: {s['status']:<12}  │  "
            f"pages: {success}ok/{failed}fail/{pending}pending (of {total})  │  "
            f"suppliers: {supplier_count}  │  "
            f"started: {s['started_at'][:19]}"
        )
    db.close()


def _cmd_export(args: argparse.Namespace) -> None:
    from thomasnet_scraper.db import Database

    db = Database(args.db)
    count = db.export_csv(args.keyword, args.output)
    if count:
        print(f"Exported {count} suppliers to {args.output}")
    else:
        print(f"No suppliers found for '{args.keyword}'")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="thomasnet_scraper",
        description="Scrape ThomasNet.com for supplier data",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared arguments added to every subparser
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    common.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    # search
    p_search = sub.add_parser("search", parents=[common], help="Search for suppliers by keyword")
    p_search.add_argument("-k", "--keyword", required=True, help="Search keyword")
    p_search.add_argument("--heading", help="Optional NAICS/heading filter")
    p_search.add_argument("--max-pages", type=int, help="Limit pages to scrape")
    p_search.add_argument(
        "--no-headless", action="store_true", help="Show browser window"
    )

    # status
    sub.add_parser("status", parents=[common], help="Show scrape session status")

    # export
    p_export = sub.add_parser("export", parents=[common], help="Export suppliers to CSV")
    p_export.add_argument("-k", "--keyword", required=True, help="Search keyword")
    p_export.add_argument("-o", "--output", required=True, help="Output CSV path")

    args = parser.parse_args()
    setup_logging(args.verbose)

    cmd_map = {
        "search": _cmd_search,
        "status": _cmd_status,
        "export": _cmd_export,
    }
    cmd_map[args.command](args)
