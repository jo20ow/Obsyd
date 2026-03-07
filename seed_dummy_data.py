"""
Seed script for fresh OBSYD clones.

Creates the SQLite databases with schema and inserts minimal dummy data
so the dashboard renders without live API keys.

Usage:
    python seed_dummy_data.py
"""

import random  # noqa: S311 — not used for security, just dummy seed data
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# ── Main database (SQLAlchemy models) ──


def seed_main_db():
    """Create obsyd.db with schema and sample chokepoint + price data."""
    db_path = Path("obsyd.db")
    if db_path.exists():
        print(f"  {db_path} already exists, skipping")
        return

    # Import after path check to avoid import-time side effects
    from backend.database import SessionLocal, init_db
    from backend.models.ports import PortActivity
    from backend.models.prices import FREDSeries

    init_db()
    db = SessionLocal()

    today = datetime.utcnow()

    # Sample chokepoint data (30 days)
    chokepoints = [
        ("chokepoint6", "Strait of Hormuz", 85, 32),
        ("chokepoint1", "Suez Canal", 62, 18),
        ("chokepoint5", "Malacca Strait", 70, 22),
        ("chokepoint2", "Panama Canal", 35, 5),
        ("chokepoint7", "Cape of Good Hope", 45, 15),
    ]

    for days_ago in range(30):
        date_str = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        for port_id, port_name, base_total, base_tanker in chokepoints:
            # Add slight variation
            variation = random.randint(-8, 8)  # nosec B311
            db.add(
                PortActivity(
                    port_id=port_id,
                    port_name=port_name,
                    date=date_str,
                    kind="chokepoint",
                    vessel_count=max(0, base_total + variation),
                    vessel_count_tanker=max(0, base_tanker + variation // 2),
                    capacity=0.0,
                    capacity_tanker=0.0,
                )
            )

    # Sample FRED price data (90 days)
    base_wti, base_brent = 68.0, 72.0
    for days_ago in range(90):
        date_str = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        drift = random.uniform(-1.5, 1.5)  # nosec B311
        db.add(
            FREDSeries(
                series_id="DCOILWTICO",
                date=date_str,
                value=round(base_wti + drift + (days_ago * 0.02), 2),
            )
        )
        db.add(
            FREDSeries(
                series_id="DCOILBRENTEU",
                date=date_str,
                value=round(base_brent + drift + (days_ago * 0.02), 2),
            )
        )

    db.commit()
    db.close()
    print(f"  Created {db_path} with sample data")


# ── PortWatch standalone database ──


def seed_portwatch_db():
    """Create data/portwatch.db with schema and sample data."""
    db_path = Path("data/portwatch.db")
    if db_path.exists():
        print(f"  {db_path} already exists, skipping")
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chokepoint_daily (
            portid TEXT NOT NULL,
            portname TEXT,
            date TEXT NOT NULL,
            n_total INTEGER DEFAULT 0,
            n_tanker INTEGER DEFAULT 0,
            capacity REAL DEFAULT 0,
            capacity_tanker REAL DEFAULT 0,
            PRIMARY KEY (portid, date)
        );
        CREATE TABLE IF NOT EXISTS port_daily (
            portid TEXT NOT NULL,
            portname TEXT,
            date TEXT NOT NULL,
            portcalls INTEGER DEFAULT 0,
            portcalls_tanker INTEGER DEFAULT 0,
            import_total REAL DEFAULT 0,
            export_total REAL DEFAULT 0,
            import_tanker REAL DEFAULT 0,
            export_tanker REAL DEFAULT 0,
            PRIMARY KEY (portid, date)
        );
        CREATE TABLE IF NOT EXISTS disruptions (
            event_id TEXT PRIMARY KEY,
            event_name TEXT,
            event_type TEXT,
            alertlevel INTEGER DEFAULT 0,
            start_date TEXT,
            end_date TEXT,
            affected_portid TEXT,
            affected_portname TEXT,
            country TEXT,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS oil_prices (
            series_id TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (series_id, date)
        );
    """)

    conn.commit()
    conn.close()
    print(f"  Created {db_path} with empty schema")


if __name__ == "__main__":
    print("OBSYD — Seeding databases\n")
    seed_main_db()
    seed_portwatch_db()
    print("\nDone. Run: uvicorn backend.main:app --reload")
