"""CLI for DirectIndustry scraper."""

import argparse
import asyncio
import sys

from directindustry_scraper.search_scraper import scrape_search
from directindustry_scraper.utils import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="DirectIndustry Scraper CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Search command
    search_parser = subparsers.add_parser("search", help="Scrape search results")
    search_parser.add_argument("keyword", help="Search keyword")
    search_parser.add_argument("--max-pages", type=int, help="Maximum pages to scrape")
    search_parser.add_argument("--no-headless", action="store_false", dest="headless", default=True,
                               help="Run browser in headed mode (visible)")
    search_parser.add_argument("--db", help="Path to SQLite database")
    search_parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "search":
        try:
            asyncio.run(scrape_search(
                keyword=args.keyword,
                max_pages=args.max_pages,
                headless=args.headless,
                db_path=args.db
            ))
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            sys.exit(1)
        except Exception as e:
            print(f"\nError: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
