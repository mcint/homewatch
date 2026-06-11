# Reference — data migrations

How schema/data evolution works today, and the plan for "graceful" migrations.

## Today (works, forward-only)

`homewatch/db.py` runs numbered `migrations/NNN_*.sql` files in order, recording
applied versions in `schema_version`; `get_db()` bootstraps + migrates on every
open (idempotent). This is **not** table-per-version — one set of tables,
evolved in place. Migrations may carry **data** changes, not just schema:

- `001_init` — schema.
- `002_devices` — `devices` table + `ALTER TABLE probes ADD COLUMN …`.
- `003_absolute_urls` — pure **data backfill** (`UPDATE releases SET url = …`).

So data migrations already fit the model: just write the `UPDATE`/backfill in a
numbered file. Keep them **idempotent** (safe to re-run, `COALESCE`/`WHERE x IS
NULL` guards) since the runner applies each version exactly once but you want
re-runs during dev to be harmless.

## Under-specified / under-provisioned data

The recurring problem: an older row predates a column, or a value can't be known
upstream (HomePod dates, network context). Patterns we use, in preference order:

1. **Nullable + derive at read time.** Don't fabricate. e.g. `released_at` stays
   NULL for HomePod; `timeline.derive_date` computes an effective date (≈tvOS,
   else ≤bound) at query time. New code, no migration.
2. **Idempotent backfill migration.** When a value *can* be computed from
   existing rows, do it in a numbered `.sql` (like `003`). Guard with `WHERE …
   IS NULL` so it only touches gaps.
3. **A `repair`/`backfill` command.** For backfills needing Python/network
   (re-deriving from a source), a CLI verb beats SQL — explicit, re-runnable,
   reportable. (Not built yet; the slot is there.)

## Plan for "graceful" (when we want more than forward-only)

The numbered-`.sql` runner is the right weight for a single-file SQLite tool —
no ORM, so SQLAlchemy/Alembic would be overkill. To make it graceful:

- **Backup before migrate.** Copy `homewatch.sqlite` → `….pre-NNN.bak` before
  applying a new version (cheap; SQLite is one file). Easy safety net without
  down-migrations.
- **Down/rollback (optional).** If reversibility is wanted, adopt paired
  `NNN_up.sql` / `NNN_down.sql`, or a tool that does it: **yoyo-migrations**
  (Python, up/down, SQLite), **sqitch** (deploy/revert/**verify**, DB-agnostic),
  or **dbmate** (single binary). Lean yoyo if we want to stay in-process.
- **`schema_version` already gives us the version vector**; a `homewatch migrate
  --status` / `--to N` surface could expose it.

Recommendation: stay with numbered `.sql` + idempotent backfills + a
backup-before-migrate step; reach for yoyo only if real rollback becomes a need.
