"""SQLite database management and state persistence for Sweepstakes Helper."""

import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from models.sweepstake import SweepstakeItem, SweepstakeStatus
from utils.logger import logger


class Database:
    """Manages SQLite persistence, schema migrations, and deduplication queries."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a configured SQLite connection with row factory enabled."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initializes tables and indexes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sweepstakes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    deadline TEXT,
                    eligibility_country TEXT DEFAULT 'AT',
                    requires_manual_entry INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'PENDING',
                    is_daily_recurring INTEGER DEFAULT 0,
                    quiz_answer TEXT,
                    error_log TEXT,
                    last_attempted_at TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(source, external_id)
                );
                """
            )
            # Create indexes for fast lookup and queue ordering
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sweepstakes_status ON sweepstakes(status);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sweepstakes_source_ext ON sweepstakes(source, external_id);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sweepstakes_requires_manual ON sweepstakes(requires_manual_entry);"
            )
            conn.commit()
            logger.debug(f"Database initialized at {self.db_path}")

    def insert_or_update_item(self, item: SweepstakeItem) -> bool:
        """Inserts a new sweepstake or updates an existing record for recurring entries.

        Returns:
            True if a new record was inserted or eligible recurring item queued, False if deduplicated/ignored.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        today_date = date.today().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, status, is_daily_recurring, last_attempted_at, completed_at FROM sweepstakes WHERE source = ? AND external_id = ?",
                (item.source, item.external_id),
            )
            existing = cursor.fetchone()

            if existing:
                row_id = existing["id"]
                current_status = existing["status"]
                is_recurring = bool(existing["is_daily_recurring"] or item.is_daily_recurring)
                last_attempt = existing["last_attempted_at"]

                # Deduplication logic:
                # If completed and daily recurring, check if last attempt was prior to today
                if current_status == SweepstakeStatus.COMPLETED and is_recurring:
                    last_date = last_attempt[:10] if last_attempt else ""
                    if last_date < today_date:
                        # Reset to PENDING for today's daily recurring entry
                        cursor.execute(
                            """
                            UPDATE sweepstakes
                            SET status = 'PENDING',
                                is_daily_recurring = 1,
                                quiz_answer = COALESCE(?, quiz_answer),
                                deadline = COALESCE(?, deadline)
                            WHERE id = ?
                            """,
                            (item.quiz_answer, item.deadline, row_id),
                        )
                        conn.commit()
                        logger.info(f"Re-queued daily recurring sweepstake: '{item.title}' (ID {row_id})")
                        return True
                    return False  # Already completed today

                # If already COMPLETED or SKIPPED, do not re-process
                if current_status in (SweepstakeStatus.COMPLETED, SweepstakeStatus.SKIPPED):
                    return False

                # If already exists and PENDING or FAILED, update quiz_answer and deadline if newly found
                cursor.execute(
                    """
                    UPDATE sweepstakes
                    SET title = ?,
                        url = ?,
                        deadline = COALESCE(?, deadline),
                        quiz_answer = COALESCE(?, quiz_answer),
                        is_daily_recurring = ?
                    WHERE id = ?
                    """,
                    (
                        item.title,
                        item.url,
                        item.deadline,
                        item.quiz_answer,
                        1 if item.is_daily_recurring else 0,
                        row_id,
                    ),
                )
                conn.commit()
                return False

            # Insert brand new sweepstake
            cursor.execute(
                """
                INSERT INTO sweepstakes (
                    source, external_id, url, title, deadline,
                    eligibility_country, requires_manual_entry, status,
                    is_daily_recurring, quiz_answer, error_log,
                    last_attempted_at, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.source,
                    item.external_id,
                    item.url,
                    item.title,
                    item.deadline,
                    item.eligibility_country,
                    1 if item.requires_manual_entry else 0,
                    item.status.value,
                    1 if item.is_daily_recurring else 0,
                    item.quiz_answer,
                    item.error_log,
                    item.last_attempted_at.isoformat() if item.last_attempted_at else None,
                    item.created_at.isoformat() if item.created_at else now_str,
                    item.completed_at.isoformat() if item.completed_at else None,
                ),
            )
            conn.commit()
            logger.debug(f"Inserted new sweepstake: '{item.title}' [{item.source}]")
            return True

    def get_pending_items(self, limit: int = 50, only_automated: bool = True) -> List[SweepstakeItem]:
        """Retrieves sweepstakes waiting for automated entry.

        Args:
            limit: Maximum count to return.
            only_automated: If True, only returns items where requires_manual_entry == 0.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if only_automated:
                cursor.execute(
                    """
                    SELECT * FROM sweepstakes
                    WHERE status = 'PENDING' AND requires_manual_entry = 0
                    ORDER BY id ASC LIMIT ?
                    """,
                    (limit,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM sweepstakes
                    WHERE status = 'PENDING'
                    ORDER BY id ASC LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            return [self._row_to_item(row) for row in rows]

    def get_item_by_id(self, item_id: int) -> Optional[SweepstakeItem]:
        """Retrieves a single sweepstake item by its database primary key ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sweepstakes WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            return self._row_to_item(row) if row else None

    def get_manual_items(self, limit: int = 50) -> List[SweepstakeItem]:
        """Retrieves sweepstakes flagged as requiring manual / interactive submission."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM sweepstakes
                WHERE requires_manual_entry = 1 AND status = 'PENDING'
                ORDER BY id ASC LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [self._row_to_item(row) for row in rows]

    def mark_completed(self, item_id: int) -> None:
        """Marks a sweepstake as successfully entered."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sweepstakes
                SET status = 'COMPLETED',
                    completed_at = ?,
                    last_attempted_at = ?,
                    error_log = NULL
                WHERE id = ?
                """,
                (now_str, now_str, item_id),
            )
            conn.commit()
            logger.info(f"Sweepstake ID {item_id} marked as COMPLETED.")

    def mark_failed(self, item_id: int, error_log: str) -> None:
        """Marks a sweepstake as failed with error details."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sweepstakes
                SET status = 'FAILED',
                    last_attempted_at = ?,
                    error_log = ?
                WHERE id = ?
                """,
                (now_str, error_log, item_id),
            )
            conn.commit()
            logger.warning(f"Sweepstake ID {item_id} marked as FAILED: {error_log[:100]}")

    def mark_requires_manual(self, item_id: int, reason: str) -> None:
        """Flags a sweepstake for interactive helper / manual user entry."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sweepstakes
                SET requires_manual_entry = 1,
                    last_attempted_at = ?,
                    error_log = ?
                WHERE id = ?
                """,
                (now_str, f"Requires manual action: {reason}", item_id),
            )
            conn.commit()
            logger.info(f"Sweepstake ID {item_id} flagged for manual interaction: {reason}")

    def mark_skipped(self, item_id: int, reason: str) -> None:
        """Marks a sweepstake as skipped (e.g. ineligible or expired)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sweepstakes
                SET status = 'SKIPPED',
                    last_attempted_at = ?,
                    error_log = ?
                WHERE id = ?
                """,
                (now_str, reason, item_id),
            )
            conn.commit()
            logger.info(f"Sweepstake ID {item_id} marked as SKIPPED: {reason}")

    def get_stats(self) -> Dict[str, int]:
        """Returns count statistics grouped by status and manual flag."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    status,
                    COUNT(*) as count
                FROM sweepstakes
                GROUP BY status
                """
            )
            stats = {row["status"]: row["count"] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE requires_manual_entry = 1 AND status = 'PENDING'"
            )
            stats["MANUAL_PENDING"] = cursor.fetchone()["count"]

            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE is_daily_recurring = 1"
            )
            stats["DAILY_RECURRING"] = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM sweepstakes")
            stats["TOTAL"] = cursor.fetchone()["count"]

            return stats

    def get_today_summary(self) -> Dict[str, Any]:
        """Generates a summary of actions executed today."""
        today_prefix = date.today().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Completed today
            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE completed_at LIKE ?",
                (f"{today_prefix}%",),
            )
            completed_today = cursor.fetchone()["count"]

            # Discovered today
            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE created_at LIKE ?",
                (f"{today_prefix}%",),
            )
            discovered_today = cursor.fetchone()["count"]

            # Failed today
            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE status = 'FAILED' AND last_attempted_at LIKE ?",
                (f"{today_prefix}%",),
            )
            failed_today = cursor.fetchone()["count"]

            # Pending manual
            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE requires_manual_entry = 1 AND status = 'PENDING'"
            )
            pending_manual = cursor.fetchone()["count"]

            # Pending automated
            cursor.execute(
                "SELECT COUNT(*) as count FROM sweepstakes WHERE requires_manual_entry = 0 AND status = 'PENDING'"
            )
            pending_automated = cursor.fetchone()["count"]

            return {
                "date": today_prefix,
                "discovered_today": discovered_today,
                "completed_today": completed_today,
                "failed_today": failed_today,
                "pending_automated": pending_automated,
                "pending_manual": pending_manual,
            }

    def remove_instagram_post_sweepstakes(self) -> int:
        """Deletes Instagram sweepstakes that require a post/interaction from the database.

        Returns:
            Number of deleted records.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM sweepstakes
                WHERE (title LIKE '%instagram%' OR url LIKE '%instagram%')
                  AND (title LIKE '%beitrag%' OR title LIKE '%post%' OR url LIKE '%/p/%' OR url LIKE '%/reel/%' OR title LIKE '%teilen%');
                """
            )
            count = cursor.rowcount
            conn.commit()
            if count > 0:
                logger.info(f"Removed {count} Instagram sweepstakes requiring a post from database.")
            return count

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> SweepstakeItem:
        """Converts an SQLite row into a SweepstakeItem model."""
        def parse_dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return None

        return SweepstakeItem(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            url=row["url"],
            title=row["title"],
            deadline=row["deadline"],
            eligibility_country=row["eligibility_country"],
            requires_manual_entry=bool(row["requires_manual_entry"]),
            status=SweepstakeStatus(row["status"]),
            is_daily_recurring=bool(row["is_daily_recurring"]),
            quiz_answer=row["quiz_answer"],
            error_log=row["error_log"],
            last_attempted_at=parse_dt(row["last_attempted_at"]),
            created_at=parse_dt(row["created_at"]) or datetime.now(timezone.utc),
            completed_at=parse_dt(row["completed_at"]),
        )
