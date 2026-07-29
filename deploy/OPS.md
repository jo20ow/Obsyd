# OBSYD ops runbook — backups, heartbeat, deep-history backfill

Companion to the scripts in this directory. Everything here runs on the VPS as the
`obsyd` user with the repo at `/home/obsyd/obsyd` (override paths via the env vars
documented in each script's header).

## 1. Cron lines (obsyd user's crontab)

```cron
# Daily consistent DB snapshot + .env copy + rotation (04:10 UTC — after the
# nightly 23:45/23:50 recompute jobs, before the 09:00 UTC watchdog):
10 4 * * * /home/obsyd/obsyd/deploy/backup-obsyd.sh >> /home/obsyd/obsyd/logs/backup-obsyd.log 2>&1

# Liveness dead-man ping every 10 minutes (probes https://obsyd.dev/api/v1/meta,
# pings healthchecks ONLY on HTTP 2xx — so one check catches VPS death AND service death):
*/10 * * * * /home/obsyd/obsyd/deploy/heartbeat.sh
```

Both lines are safe to install BEFORE the healthchecks.io account exists: an unset
ping URL means "no ping, exit 0" (heartbeat) / "back up anyway, skip the ping"
(backup). Do NOT also cron the old `deploy/backup-db.sh` — `backup-obsyd.sh`
supersedes it (sqlite3.backup() API instead of the sqlite3 CLI, plus rotation).

## 2. Owner TODO — healthchecks.io

1. Create a free https://healthchecks.io account and TWO checks:
   * **obsyd-backup** — period 1 day, grace ~2 h (the 04:10 UTC cron).
   * **obsyd-live** — period 10 min, grace ~15 min (the heartbeat cron).
2. Put both ping URLs into the prod `.env` (`/home/obsyd/obsyd/.env`):

   ```
   HEALTHCHECKS_BACKUP_URL=https://hc-ping.com/<uuid-of-obsyd-backup>
   HEALTHCHECKS_LIVE_URL=https://hc-ping.com/<uuid-of-obsyd-live>
   ```

   The scripts read them from the environment first, then from that `.env` —
   no service restart needed (cron re-reads the file every run).
3. Offsite: the snapshots in `/home/obsyd/backups/` are LOCAL. Pull the newest
   one to a workstation regularly (the first line of the restore recipe below
   doubles as the offsite copy) — a dead disk takes the backups with it otherwise.

## 3. Restore recipe

```sh
# workstation: fetch + sanity-check a snapshot (also your offsite copy)
scp <admin>@<vps>:/home/obsyd/backups/obsyd-YYYYMMDD.db.gz .
gunzip -k obsyd-YYYYMMDD.db.gz     # keep the .gz; inspect the .db locally if in doubt

# VPS: stop, swap, start
sudo systemctl stop obsyd
sudo -u obsyd gunzip -kc /home/obsyd/backups/obsyd-YYYYMMDD.db.gz \
    > /home/obsyd/obsyd/obsyd.db.restored
sudo -u obsyd mv /home/obsyd/obsyd/obsyd.db.restored /home/obsyd/obsyd/obsyd.db
sudo -u obsyd rm -f /home/obsyd/obsyd/obsyd.db-wal /home/obsyd/obsyd/obsyd.db-shm
sudo systemctl start obsyd
# verify: https://obsyd.dev/api/v1/status
```

The matching `env-YYYYMMDD` file in the backup dir is the day's `.env` (chmod 600)
— restore it alongside if the secrets were lost too.

## 4. Uniform-2019 deep-history backfill (one-time, per series family)

Goal: pull every lagging series back to a uniform 2019-01-01 line. ENTSO-E history
is immutable — fetch once (raw-cached), keep forever. `price`/`grid`/`forecast`
already carry deep history from earlier runs; the laggards are the sources below.

Run SEQUENTIALLY (one source at a time — they all share the one ENTSO-E token and
the one SQLite writer), as the `obsyd` user, from `/home/obsyd/obsyd`. Sanity-check
the plan first with `--dry-run`. Every write is an upsert and every payload is
disk-cached, so a crashed run resumes from cache for free.

```sh
cd /home/obsyd/obsyd
nohup bash -c 'for s in flows scheduled netpos ntc units_gen imbalance balancing; do
  .venv/bin/python -m backend.scripts.power_backfill --sources "$s" --start 2019-01-01 || exit 1
done' >> logs/backfill-2019.log 2>&1 &
tail -f logs/backfill-2019.log
```

Then — LAST and ALONE, only after the loop above has finished — capacity prices
(A15 paginates extremely heavily, ~200+ requests per calendar DAY; never bundle it,
and keep its tighter start):

```sh
nohup .venv/bin/python -m backend.scripts.power_backfill --sources capacity --start 2024-01-01 \
    >> logs/backfill-capacity.log 2>&1 &
```

Notes:

* Order rationale: cheap zone-independent sweeps first (`flows`, `scheduled`,
  `netpos`, `ntc`), then the bounded per-CTA drill-down (`units_gen`, ~240
  requests/extra year — acceptable sequentially for the uniform 2019 line, which
  is why this runbook overrides the module docstring's 2025 floor), then the
  per-zone month sweeps (`imbalance`, `balancing`). `balancing` absorbs
  pre-availability years cheaply: the collector caches ENTSO-E's documented
  "genuinely nothing here" 400 phrases as emptiness, so each empty zone-month
  costs one request, once.
* `--sources capacity --start 2024-01-01` is a hard recommendation, not a
  default: it is deliberately NOT in `ALL_SOURCES` (see
  `backend/scripts/power_backfill.py`'s module docstring), and every extra year
  is ~70k more requests.
* No recompute step needed afterwards: the all-time records (23:45 UTC) and
  grid-stress episodes (23:50 UTC) jobs are full nightly recomputes over the
  canonical series — the new history folds into records/episodes on its own
  within a day.
* The backfill can collide with the live scheduler on SQLite's single writer;
  `_with_retry` in the backfill absorbs transient "database is locked" errors,
  so just let it run.
