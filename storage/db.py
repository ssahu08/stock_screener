"""
DuckDB Storage Module
=====================

Persistent storage for screener results.
Handles:
  - Initializing the database (creating tables if needed)
  - Migrating the legacy 11-column schema to the current 16-column schema
  - Saving scan results (insert by column NAME, order-independent)
  - Pruning old scans (keeping last N per strategy)
  - Querying historical data / strategy statistics

Database file: data/screener.duckdb (gitignored for safety)
"""

import os
import sys
import datetime

import duckdb
import pandas as pd

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Database path: BASE_DIR / data / screener.duckdb
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "screener.duckdb")

# Canonical schema — every scan row MUST contain exactly these columns.
# Order matters only for DataFrame construction; INSERT uses names.
SCAN_COLUMNS = [
    "scan_id",
    "scan_time",
    "strategy_name",
    "ticker",
    "current_price",
    "signal",
    "key_metrics",
    "weekly_change_pct",
    "forward_4w_ret",
    "hist_signal_count",
    "hist_avg_return",
    "hist_signal_dates",
    "zone_breakout",
    "profit_taking_warning",
    "breakout_direction",
    "stop_loss_price",
]

# SQL types for columns that may need migrating (legacy 11-col table)
COLUMN_TYPES = {
    "scan_id": "VARCHAR",
    "scan_time": "TIMESTAMP",
    "strategy_name": "VARCHAR",
    "ticker": "VARCHAR",
    "current_price": "DOUBLE",
    "signal": "VARCHAR",
    "key_metrics": "VARCHAR",
    "weekly_change_pct": "DOUBLE",
    "forward_4w_ret": "DOUBLE",
    "hist_signal_count": "INTEGER",
    "hist_avg_return": "DOUBLE",
    "hist_signal_dates": "VARCHAR",
    "zone_breakout": "BOOLEAN",
    "profit_taking_warning": "BOOLEAN",
    "breakout_direction": "VARCHAR",
    "stop_loss_price": "DOUBLE",
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _ensure_schema(con) -> None:
    """Create table if missing, and ALTER-ADD any columns the legacy
    schema (11 columns) is missing relative to SCAN_COLUMNS."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS multi_strategy_scans (
            scan_id VARCHAR,
            scan_time TIMESTAMP,
            strategy_name VARCHAR,
            ticker VARCHAR,
            current_price DOUBLE,
            signal VARCHAR,
            key_metrics VARCHAR,
            weekly_change_pct DOUBLE,
            forward_4w_ret DOUBLE,
            hist_signal_count INTEGER,
            hist_avg_return DOUBLE,
            hist_signal_dates VARCHAR,
            zone_breakout BOOLEAN,
            profit_taking_warning BOOLEAN,
            breakout_direction VARCHAR,
            stop_loss_price DOUBLE
        )
    """)

    existing = {
        row[0]
        for row in con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'multi_strategy_scans'
            """
        ).fetchall()
    }

    for col in SCAN_COLUMNS:
        if col not in existing:
            con.execute(
                f"ALTER TABLE multi_strategy_scans ADD COLUMN {col} {COLUMN_TYPES[col]}"
            )
            print(f"🧩 Migrated DB schema: added column '{col}'")

    # Monthly Open=Low review table (owner brief point 2) — additive,
    # idempotent; never touches multi_strategy_scans.
    con.execute("""
        CREATE TABLE IF NOT EXISTS monthly_open_low (
            symbol VARCHAR,
            trade_month DATE,
            entry_date DATE,
            entry_open DOUBLE,
            entry_low DOUBLE,
            entry_close DOUBLE,
            close_price DOUBLE,
            high_since DOUBLE,
            low_since DOUBLE,
            current_return_pct DOUBLE,
            max_return_pct DOUBLE,
            max_drawdown_pct DOUBLE,
            status VARCHAR,          -- ACTIVE | CHECKED | CLOSED
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_monthly_open_low
        ON monthly_open_low (symbol, trade_month)
    """)


def _get_connection():
    """Get a connected DuckDB instance, creating/migrating schema as needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)
    _ensure_schema(con)
    return con


def get_connection():
    """Public wrapper around _get_connection (same single-writer rules:
    close before another writer opens — DuckDB allows one at a time)."""
    return _get_connection()


def _frame_in_canonical_order(rows) -> pd.DataFrame:
    """Build a DataFrame that contains exactly SCAN_COLUMNS, in order."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in SCAN_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[SCAN_COLUMNS]


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def init_db() -> None:
    """Initialize/migrate the database. Idempotent — safe to call repeatedly."""
    con = _get_connection()
    con.close()
    print("💾 Database ready at:", DB_PATH)


def save_scan_results(results: dict, strategy_name: str) -> None:
    """Save a scan's results to the DuckDB database.

    Args:
        results: dict with "all_results" (and optionally "buy_signals"),
                 each item a row dict; plus "scan_id" / "scan_time".
        strategy_name: Name of the strategy (e.g. "Hilega Milega")
    """
    con = _get_connection()

    try:
        rows = results.get("all_results") or results.get("buy_signals") or []
        if not rows:
            print("⚠️  No data to save to DB")
            return

        # Tag rows (assignment preserves existing key order)
        for row in rows:
            row["strategy_name"] = strategy_name
            row["scan_id"] = results.get("scan_id", "")
            row["scan_time"] = results.get("scan_time", datetime.datetime.now())

        df = _frame_in_canonical_order(rows)
        con.register("new_results", df)

        # Insert by column NAME — works regardless of physical column order,
        # which matters because ALTER TABLE appends migrated columns at the end.
        cols = ", ".join(SCAN_COLUMNS)
        con.execute(
            f"INSERT INTO multi_strategy_scans ({cols}) SELECT {cols} FROM new_results"
        )

        _prune_old_scans(con, strategy_name, keep=2)
        print(f"💾 Saved {len(df)} records to DuckDB")

    except Exception as e:
        print(f"❌ Error saving to DB: {e}")
    finally:
        con.close()


def _prune_old_scans(con, strategy_name: str, keep: int = 2) -> None:
    """Keep only the K most recent scans per strategy, delete the rest.
    scan_id is timestamp-formatted, so DESC string order = newest first."""
    try:
        distinct_scans = con.execute(
            """
            SELECT DISTINCT scan_id FROM multi_strategy_scans
            WHERE strategy_name = ?
            ORDER BY scan_id DESC
            """,
            [strategy_name],
        ).fetchall()

        all_scan_ids = [row[0] for row in distinct_scans]
        if len(all_scan_ids) <= keep:
            return

        scan_ids_to_delete = all_scan_ids[keep:]
        placeholders = ", ".join(["?"] * len(scan_ids_to_delete))
        con.execute(
            f"""
            DELETE FROM multi_strategy_scans
            WHERE strategy_name = ?
              AND scan_id NOT IN ({placeholders})
            """,
            [strategy_name] + scan_ids_to_delete,
        )
        print(
            f"🧹 Pruned {len(scan_ids_to_delete)} old scan(s), "
            f"keeping {keep} most recent for '{strategy_name}'"
        )

    except Exception as e:
        print(f"⚠️  Error pruning old scans: {e}")


def get_scan_history(
    strategy_name: str = None,
    ticker: str = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Query historical scan data. Newest scan first."""
    con = _get_connection()
    try:
        cols = ", ".join(SCAN_COLUMNS)
        query = f"SELECT {cols} FROM multi_strategy_scans WHERE 1=1"
        params = []

        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)

        query += " ORDER BY scan_id DESC, ticker LIMIT ?"
        params.append(limit)

        return con.execute(query, params).fetchdf()
    except Exception as e:
        print(f"❌ Error querying DB: {e}")
        return pd.DataFrame()
    finally:
        con.close()


def get_strategy_stats(strategy_name: str) -> dict:
    """Aggregate statistics for a strategy from all stored scans."""
    con = _get_connection()
    try:
        total, buys, sells, holds = con.execute(
            """
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE signal = 'BUY'),
                COUNT(*) FILTER (WHERE signal = 'SELL'),
                COUNT(*) FILTER (WHERE signal = 'HOLD')
            FROM multi_strategy_scans
            WHERE strategy_name = ?
            """,
            [strategy_name],
        ).fetchone()

        # Forward-return stats across BUY rows that have backtest data
        n_eval, n_win, avg_ret = con.execute(
            """
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE forward_4w_ret > 0),
                AVG(forward_4w_ret)
            FROM multi_strategy_scans
            WHERE strategy_name = ?
              AND signal = 'BUY'
              AND forward_4w_ret IS NOT NULL
            """,
            [strategy_name],
        ).fetchone()

        win_rate = (n_win / n_eval * 100) if n_eval else 0.0
        last_scan = con.execute(
            "SELECT MAX(scan_time) FROM multi_strategy_scans WHERE strategy_name = ?",
            [strategy_name],
        ).fetchone()[0]

        return {
            "total_scans": total or 0,
            "buy_signals": buys or 0,
            "sell_signals": sells or 0,
            "hold_signals": holds or 0,
            "evaluated_buys": n_eval or 0,
            "avg_forward_4w_return": round(float(avg_ret), 2) if avg_ret else 0.0,
            "win_rate_pct": round(float(win_rate), 1),
            "last_scan": str(last_scan) if last_scan else "Never",
        }
    except Exception as e:
        print(f"❌ Error getting strategy stats: {e}")
        return {}
    finally:
        con.close()
