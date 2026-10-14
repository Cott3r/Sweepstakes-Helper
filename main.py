"""Main CLI batch processor and entry point for Sweepstakes Helper Austria."""

import argparse
import asyncio
import sys
from typing import List

from automation.browser import BrowserManager
from automation.runner import AutomationRunner
from config.settings import settings, load_user_profile
from database.db import Database
from models.sweepstake import SweepstakeItem, SweepstakeStatus
from notifications.notifier import Notifier
from scrapers.gewinnspielsammlung import GewinnspielsammlungScraper
from scrapers.reddit import RedditSweepstakesScraper
from utils.logger import logger, setup_logger


async def run_scrapers(db: Database) -> int:
    """Runs all registered scrapers, filters for Austria eligibility, and deduplicates against the database.

    Returns:
        Count of newly inserted or re-queued sweepstakes.
    """
    scrapers = [
        GewinnspielsammlungScraper(),
        RedditSweepstakesScraper(),
    ]

    total_new = 0

    for scraper in scrapers:
        logger.info(f"===> Running {scraper.name} scraper...")
        try:
            items: List[SweepstakeItem] = await scraper.scrape()
            new_for_scraper = 0
            for item in items:
                is_new = db.insert_or_update_item(item)
                if is_new:
                    new_for_scraper += 1

            total_new += new_for_scraper
            logger.info(f"Finished {scraper.name}: Discovered {len(items)}, {new_for_scraper} newly added to database.")
        except Exception as exc:
            logger.error(f"Error executing scraper {scraper.name}: {exc}")

    return total_new


async def cmd_run(args):
    """Executes the complete daily workflow: Scrape -> Deduplicate -> Enter -> Notify."""
    logger.info("Starting Daily Sweepstakes Batch Process...")
    profile = load_user_profile(settings.USER_PROFILE_PATH)
    db = Database(settings.DB_PATH)
    notifier = Notifier()

    # Step 1: Scrape & Deduplicate
    new_count = await run_scrapers(db)
    logger.info(f"Scraping stage complete. Added {new_count} new entries to database.")

    # Step 2: Automated entry execution
    runner = AutomationRunner(db, profile)
    interactive = not args.no_interactive and settings.INTERACTIVE_FALLBACK
    summary = await runner.process_batch(limit=args.limit, allow_interactive=interactive)

    # Step 3: Fetch today summary & notify
    today_summary = db.get_today_summary()
    logger.info(
        f"Batch summary: {summary['completed']} completed, "
        f"{summary['flagged_manual']} manual actions required, "
        f"{summary['failed']} failed."
    )

    await notifier.send_daily_summary(today_summary)
    print("\nDaily batch process completed successfully.")


async def cmd_scrape(args):
    """Scrapes sources and updates database without submitting forms."""
    logger.info("Executing Scrape-Only Mode...")
    db = Database(settings.DB_PATH)
    new_count = await run_scrapers(db)
    stats = db.get_stats()
    print("\n" + "=" * 50)
    print("SCRAPE RESULTS SUMMARY")
    print("=" * 50)
    print(f"New Sweepstakes Added Today: {new_count}")
    print(f"Total Sweepstakes in DB:     {stats.get('TOTAL', 0)}")
    print(f"Pending Automated Entries:   {stats.get('PENDING', 0) - stats.get('MANUAL_PENDING', 0)}")
    print(f"Pending Manual Entries:      {stats.get('MANUAL_PENDING', 0)}")
    print("=" * 50)


async def cmd_enter(args):
    """Processes pending automated entries from database."""
    logger.info("Executing Entry Processing Mode...")
    profile = load_user_profile(settings.USER_PROFILE_PATH)
    db = Database(settings.DB_PATH)
    runner = AutomationRunner(db, profile)

    interactive = not args.no_interactive and settings.INTERACTIVE_FALLBACK
    summary = await runner.process_batch(limit=args.limit, allow_interactive=interactive)
    print("\n" + "=" * 50)
    print("AUTOMATION PROCESSING SUMMARY")
    print("=" * 50)
    print(f"Processed:              {summary['processed']}")
    print(f"Completed Successfully: {summary['completed']}")
    print(f"Flagged Manual Action:  {summary['flagged_manual']}")
    print(f"Failed:                 {summary['failed']}")
    print("=" * 50)


async def cmd_manual(args):
    """Interactive assistant mode: launches browser for sweepstakes requiring manual entry."""
    logger.info("Launching Manual / Interactive Helper Mode...")
    profile = load_user_profile(settings.USER_PROFILE_PATH)
    db = Database(settings.DB_PATH)
    runner = AutomationRunner(db, profile)

    manual_items = db.get_manual_items(limit=args.limit or 20)
    if not manual_items:
        print("\nNo pending sweepstakes require manual action! You are all caught up.")
        return

    print(f"\nFound {len(manual_items)} sweepstakes requiring manual action.")
    completed = 0
    skipped = 0

    for item in manual_items:
        status = await runner.run_single_interactive(item)
        if status == SweepstakeStatus.COMPLETED:
            completed += 1
        elif status == SweepstakeStatus.SKIPPED:
            skipped += 1

    print("\n" + "=" * 50)
    print("MANUAL SESSION COMPLETE")
    print("=" * 50)
    print(f"Completed: {completed}")
    print(f"Skipped:   {skipped}")
    print("=" * 50)


async def cmd_status(args):
    """Displays current statistics and database contents."""
    db = Database(settings.DB_PATH)
    stats = db.get_stats()
    today_summary = db.get_today_summary()

    print("\n" + "=" * 60)
    print("  SWEEPSTAKES HELPER (AUSTRIA) - DATABASE STATUS")
    print("=" * 60)
    print(f"Total Sweepstakes:         {stats.get('TOTAL', 0)}")
    print(f"Completed:                 {stats.get('COMPLETED', 0)}")
    print(f"Pending (Automated):       {stats.get('PENDING', 0) - stats.get('MANUAL_PENDING', 0)}")
    print(f"Pending (Manual Required): {stats.get('MANUAL_PENDING', 0)}")
    print(f"Skipped:                   {stats.get('SKIPPED', 0)}")
    print(f"Failed:                    {stats.get('FAILED', 0)}")
    print(f"Daily Recurring Tracks:    {stats.get('DAILY_RECURRING', 0)}")
    print("-" * 60)
    print(f"Today Discovered:          {today_summary.get('discovered_today', 0)}")
    print(f"Today Completed:           {today_summary.get('completed_today', 0)}")
    print("=" * 60)


async def cmd_test_browser(args):
    """Sanity test for the stealth Playwright browser environment."""
    logger.info("Testing Playwright stealth browser configuration...")
    async with BrowserManager(headless=True) as mgr:
        page = await mgr.create_stealth_page()
        test_url = "https://bot.sannysoft.com/"
        logger.info(f"Navigating to anti-bot benchmark: {test_url}")
        await page.goto(test_url, wait_until="networkidle", timeout=20000)
        webdriver_value = await page.evaluate("() => navigator.webdriver")
        languages = await page.evaluate("() => navigator.languages")
        user_agent = await page.evaluate("() => navigator.userAgent")

        print("\n" + "=" * 50)
        print("BROWSER STEALTH SANITY TEST")
        print("=" * 50)
        print(f"navigator.webdriver: {webdriver_value} (Should be False/None)")
        print(f"navigator.languages: {languages}")
        print(f"User Agent:          {user_agent[:60]}...")
        print("=" * 50)
        logger.info("Browser test passed successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="Daily Sweepstakes Assistant & Automated Entry Tool for Austria (AT)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Operational mode")

    # Command: run (default daily job)
    parser_run = subparsers.add_parser("run", help="Run full daily batch (scrape + filter + enter + notify)")
    parser_run.add_argument("--limit", type=int, default=settings.MAX_ENTRIES_PER_RUN, help="Maximum entries to attempt")
    parser_run.add_argument("--no-interactive", action="store_true", help="Do not prompt for interactive manual fallback")

    # Command: scrape
    subparsers.add_parser("scrape", help="Scrape new sweepstakes and update database without submitting")

    # Command: enter
    parser_enter = subparsers.add_parser("enter", help="Process pending automated entries")
    parser_enter.add_argument("--limit", type=int, default=settings.MAX_ENTRIES_PER_RUN, help="Maximum entries to attempt")
    parser_enter.add_argument("--no-interactive", action="store_true", help="Do not prompt for interactive manual fallback")

    # Command: manual
    parser_manual = subparsers.add_parser("manual", help="Launch interactive helper for contests needing human attention")
    parser_manual.add_argument("--limit", type=int, default=15, help="Number of manual contests to process")

    # Command: status
    subparsers.add_parser("status", help="Print statistics and database summary")

    # Command: test-browser
    subparsers.add_parser("test-browser", help="Sanity check stealth browser against bot detection")

    args = parser.parse_args()

    if args.debug:
        setup_logger("DEBUG")

    if not args.command:
        # Default to 'run' if no subcommand provided
        args.command = "run"
        args.limit = settings.MAX_ENTRIES_PER_RUN
        args.no_interactive = False

    cmd_map = {
        "run": cmd_run,
        "scrape": cmd_scrape,
        "enter": cmd_enter,
        "manual": cmd_manual,
        "status": cmd_status,
        "test-browser": cmd_test_browser,
    }

    try:
        asyncio.run(cmd_map[args.command](args))
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
