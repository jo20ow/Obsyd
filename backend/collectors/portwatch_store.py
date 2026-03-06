"""
Standalone PortWatch SQLite storage and query functions.

Can be used independently of the main OBSYD database for testing
and standalone data collection.

Tables: chokepoint_daily, port_daily, disruptions
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

STORE_PATH = Path(__file__).parent.parent.parent / "data" / "portwatch.db"

PORTS_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/Daily_Ports_Data/FeatureServer/0/query"
)
CHOKEPOINTS_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/Daily_Chokepoints_Data/FeatureServer/0/query"
)
DISRUPTIONS_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/portwatch_disruptions_database/FeatureServer/0/query"
)

CHOKEPOINTS = {
    "chokepoint6": "Strait of Hormuz",
    "chokepoint1": "Suez Canal",
    "chokepoint5": "Malacca Strait",
    "chokepoint2": "Panama Canal",
    "chokepoint7": "Cape of Good Hope",
}

REQUEST_TIMEOUT = 30


def _init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chokepoint_daily (
            portid TEXT NOT NULL,
            portname TEXT,
            date TEXT NOT NULL,
            n_total INTEGER DEFAULT 0,
            n_tanker INTEGER DEFAULT 0,
            capacity REAL DEFAULT 0,
            capacity_tanker REAL DEFAULT 0,
            PRIMARY KEY (portid, date)
        )
    """)
    conn.execute("""
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
        )
    """)
    conn.execute("""
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oil_prices (
            series_id TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (series_id, date)
        )
    """)
    conn.commit()
    return conn


def _parse_date(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val[:10]
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    return None


# ---------- Fetch functions ----------

def fetch_chokepoint_data(days: int = 30) -> list[dict]:
    """Fetch chokepoint daily data from IMF PortWatch."""
    cp_ids = "','".join(CHOKEPOINTS.keys())
    params = {
        "where": f"portid IN ('{cp_ids}')",
        "outFields": "portid,portname,date,n_total,n_tanker,capacity,capacity_tanker",
        "orderByFields": "date DESC",
        "resultRecordCount": str(days * len(CHOKEPOINTS)),
        "f": "json",
    }
    resp = httpx.get(CHOKEPOINTS_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    rows = []
    for f in resp.json().get("features", []):
        a = f.get("attributes", {})
        date_str = _parse_date(a.get("date"))
        if not date_str:
            continue
        rows.append({
            "portid": a.get("portid", ""),
            "portname": a.get("portname", ""),
            "date": date_str,
            "n_total": a.get("n_total") or 0,
            "n_tanker": a.get("n_tanker") or 0,
            "capacity": a.get("capacity") or 0.0,
            "capacity_tanker": a.get("capacity_tanker") or 0.0,
        })
    return rows


def fetch_port_data(port_id: str, days: int = 30) -> list[dict]:
    """Fetch port daily data from IMF PortWatch."""
    params = {
        "where": f"portid = '{port_id}'",
        "outFields": "portid,portname,date,portcalls,portcalls_tanker,"
                     "import_tanker,export_tanker,import,export",
        "orderByFields": "date DESC",
        "resultRecordCount": str(days),
        "f": "json",
    }
    resp = httpx.get(PORTS_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    rows = []
    for f in resp.json().get("features", []):
        a = f.get("attributes", {})
        date_str = _parse_date(a.get("date"))
        if not date_str:
            continue
        rows.append({
            "portid": a.get("portid", ""),
            "portname": a.get("portname", ""),
            "date": date_str,
            "portcalls": a.get("portcalls") or 0,
            "portcalls_tanker": a.get("portcalls_tanker") or 0,
            "import_total": a.get("import") or 0,
            "export_total": a.get("export") or 0,
            "import_tanker": a.get("import_tanker") or 0,
            "export_tanker": a.get("export_tanker") or 0,
        })
    return rows


def fetch_disruptions(days: int = 90) -> list[dict]:
    """Fetch disruption events from IMF PortWatch."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_year = cutoff.year
    params = {
        "where": f"year >= {cutoff_year} OR todate IS NULL",
        "outFields": "eventid,eventname,eventtype,alertlevel,fromdate,todate,"
                     "affectedports,country,htmldescription",
        "orderByFields": "fromdate DESC",
        "resultRecordCount": "500",
        "f": "json",
    }
    resp = httpx.get(DISRUPTIONS_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    alert_map = {"RED": 3, "ORANGE": 2, "GREEN": 1}
    rows = []
    for f in resp.json().get("features", []):
        a = f.get("attributes", {})
        start = _parse_date(a.get("fromdate"))
        if not start:
            continue
        rows.append({
            "event_id": str(a.get("eventid", "")),
            "event_name": a.get("eventname", ""),
            "event_type": a.get("eventtype", ""),
            "alertlevel": alert_map.get(a.get("alertlevel", ""), 0),
            "start_date": start,
            "end_date": _parse_date(a.get("todate")),
            "affected_portid": a.get("affectedports") or "",
            "affected_portname": "",
            "country": a.get("country") or "",
            "description": a.get("htmldescription") or "",
        })
    return rows


def backfill_chokepoints(since: str = "2019-01-01", batch_size: int = 1000) -> list[dict]:
    """Fetch ALL chokepoint data since a given date using ArcGIS pagination."""
    import time
    all_rows = []
    offset = 0
    batch_num = 0

    while True:
        batch_num += 1
        params = {
            "where": f"date >= '{since}'",
            "outFields": "portid,portname,date,n_total,n_tanker,capacity,capacity_tanker",
            "orderByFields": "date ASC",
            "resultRecordCount": str(batch_size),
            "resultOffset": str(offset),
            "f": "json",
        }
        resp = httpx.get(CHOKEPOINTS_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])

        if not features:
            break

        rows = []
        for f in features:
            a = f.get("attributes", {})
            date_str = _parse_date(a.get("date"))
            if not date_str:
                continue
            rows.append({
                "portid": a.get("portid", ""),
                "portname": a.get("portname", ""),
                "date": date_str,
                "n_total": a.get("n_total") or 0,
                "n_tanker": a.get("n_tanker") or 0,
                "capacity": a.get("capacity") or 0.0,
                "capacity_tanker": a.get("capacity_tanker") or 0.0,
            })

        all_rows.extend(rows)
        print(f"  Batch {batch_num}: {len(features)} features (offset {offset}, total {len(all_rows)})")

        exceeded = data.get("exceededTransferLimit", False)
        if not exceeded or len(features) < batch_size:
            break

        offset += len(features)
        time.sleep(0.5)

    return all_rows


# ---------- Store functions ----------

def store_chokepoint_data(rows: list[dict], db_path: Path | None = None):
    conn = _init_db(db_path)
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO chokepoint_daily VALUES (?,?,?,?,?,?,?)",
            (r["portid"], r["portname"], r["date"],
             r["n_total"], r["n_tanker"], r["capacity"], r["capacity_tanker"]),
        )
    conn.commit()
    conn.close()


def store_port_data(rows: list[dict], db_path: Path | None = None):
    conn = _init_db(db_path)
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO port_daily VALUES (?,?,?,?,?,?,?,?,?)",
            (r["portid"], r["portname"], r["date"],
             r["portcalls"], r["portcalls_tanker"],
             r["import_total"], r["export_total"],
             r["import_tanker"], r["export_tanker"]),
        )
    conn.commit()
    conn.close()


def store_disruptions(rows: list[dict], db_path: Path | None = None):
    conn = _init_db(db_path)
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO disruptions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["event_id"], r["event_name"], r["event_type"], r["alertlevel"],
             r["start_date"], r["end_date"],
             r["affected_portid"], r["affected_portname"],
             r["country"], r["description"]),
        )
    conn.commit()
    conn.close()


# ---------- Query functions ----------

def query_chokepoint_averages(days: int = 30, db_path: Path | None = None) -> list[dict]:
    """Average daily vessel counts per chokepoint over the last N days."""
    conn = _init_db(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    cur = conn.execute("""
        SELECT portid, portname,
               COUNT(*) as n_days,
               ROUND(AVG(n_total), 1) as avg_total,
               ROUND(AVG(n_tanker), 1) as avg_tanker,
               ROUND(AVG(capacity), 0) as avg_capacity
        FROM chokepoint_daily
        WHERE date >= ?
        GROUP BY portid
        ORDER BY avg_total DESC
    """, (cutoff,))
    rows = []
    for r in cur.fetchall():
        rows.append({
            "portid": r[0], "portname": r[1], "n_days": r[2],
            "avg_total": r[3], "avg_tanker": r[4], "avg_capacity": r[5],
        })
    conn.close()
    return rows


def query_active_disruptions(db_path: Path | None = None) -> list[dict]:
    """Get disruptions that are currently active (no end_date or end_date in the future)."""
    conn = _init_db(db_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur = conn.execute("""
        SELECT event_id, event_name, event_type, alertlevel,
               start_date, end_date, affected_portname, country
        FROM disruptions
        WHERE end_date IS NULL OR end_date >= ?
        ORDER BY alertlevel DESC, start_date DESC
    """, (today,))
    rows = []
    for r in cur.fetchall():
        rows.append({
            "event_id": r[0], "event_name": r[1], "event_type": r[2],
            "alertlevel": r[3], "start_date": r[4], "end_date": r[5],
            "affected_portname": r[6], "country": r[7],
        })
    conn.close()
    return rows


# ---------- Oil prices (FRED) ----------

FRED_OIL_SERIES = {
    "DCOILWTICO": "WTI Crude Oil",
    "DCOILBRENTEU": "Brent Crude Oil",
}


def fetch_oil_prices(days: int = 365, fred_api_key: str | None = None) -> list[dict]:
    """Fetch daily WTI + Brent prices from FRED API."""
    import os
    key = fred_api_key or os.environ.get("FRED_API_KEY", "")
    if not key:
        # Try loading from .env in project root
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("FRED_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        return []

    rows = []
    for series_id in FRED_OIL_SERIES:
        params = {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": str(days),
        }
        try:
            resp = httpx.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params=params, timeout=30,
            )
            resp.raise_for_status()
            for obs in resp.json().get("observations", []):
                val = obs.get("value", ".")
                if val == ".":
                    continue
                rows.append({
                    "series_id": series_id,
                    "date": obs["date"],
                    "value": float(val),
                })
        except Exception:
            pass
    return rows


def store_oil_prices(rows: list[dict], db_path: Path | None = None):
    conn = _init_db(db_path)
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO oil_prices VALUES (?,?,?)",
            (r["series_id"], r["date"], r["value"]),
        )
    conn.commit()
    conn.close()


def query_oil_prices(days: int = 365, db_path: Path | None = None) -> dict:
    """Return WTI + Brent as separate sorted lists."""
    conn = _init_db(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    result = {}
    for series_id, name in FRED_OIL_SERIES.items():
        cur = conn.execute("""
            SELECT date, value FROM oil_prices
            WHERE series_id = ? AND date >= ?
            ORDER BY date ASC
        """, (series_id, cutoff))
        result[series_id] = [{"date": r[0], "value": r[1]} for r in cur.fetchall()]
    conn.close()
    return result


# ---------- CLI ----------

def _print_db_stats(db_path=None):
    """Print database statistics."""
    conn = _init_db(db_path)

    total = conn.execute("SELECT COUNT(*) FROM chokepoint_daily").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM chokepoint_daily").fetchone()
    per_cp = conn.execute("""
        SELECT portid, portname, COUNT(*) as n, MIN(date) as first, MAX(date) as last
        FROM chokepoint_daily
        GROUP BY portid
        ORDER BY n DESC
    """).fetchall()

    print(f"\n  Database: {db_path or STORE_PATH}")
    print(f"  Total records: {total:,}")
    if date_range[0]:
        print(f"  Date range: {date_range[0]} to {date_range[1]}")
    print(f"\n  {'Chokepoint':<25} {'Records':>8} {'First':>12} {'Last':>12}")
    print(f"  {'-'*59}")
    for r in per_cp:
        print(f"  {r[1]:<25} {r[2]:>8,} {r[3]:>12} {r[4]:>12}")
    conn.close()


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "backfill":
        since = sys.argv[2] if len(sys.argv) > 2 else "2019-01-01"
        print(f"\n  Backfilling chokepoint data since {since}...")
        print(f"  ArcGIS paginates at 2000 records — this may take a few minutes.\n")

        rows = backfill_chokepoints(since=since)
        print(f"\n  Fetched {len(rows):,} total records")

        print("  Storing (upsert)...")
        store_chokepoint_data(rows)

        _print_db_stats()
        print()

    elif cmd == "stats":
        _print_db_stats()
        print()

    else:
        print("\n  Fetching 30 days of chokepoint data...")
        cp_data = fetch_chokepoint_data(days=30)
        print(f"  Received {len(cp_data)} records")

        store_chokepoint_data(cp_data)
        print(f"  Stored in {STORE_PATH}")

        print("\n  Fetching disruptions (90 days)...")
        dis_data = fetch_disruptions(days=90)
        print(f"  Received {len(dis_data)} disruption events")
        store_disruptions(dis_data)

        avgs = query_chokepoint_averages(days=30)
        print(f"\n  {'Chokepoint':<25} {'Days':>5} {'Avg Total':>10} {'Avg Tanker':>11} {'Avg Cap (DWT)':>14}")
        print(f"  {'-'*67}")
        for a in avgs:
            print(f"  {a['portname']:<25} {a['n_days']:>5} {a['avg_total']:>10.1f} {a['avg_tanker']:>11.1f} {a['avg_capacity']:>14,.0f}")

        active = query_active_disruptions()
        if active:
            print(f"\n  Active Disruptions ({len(active)}):")
            print(f"  {'Alert':>5} {'Type':<12} {'Name':<40} {'Port':<20}")
            print(f"  {'-'*79}")
            for d in active[:10]:
                print(f"  {d['alertlevel']:>5} {d['event_type']:<12} {d['event_name'][:38]:<40} {(d['affected_portname'] or '')[:18]:<20}")
        else:
            print("\n  No active disruptions")

        print()
