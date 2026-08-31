# Supabase setup

1. Create a Supabase project.
2. For the approved legacy reset, run `reset.sql` once after creating a backup.
3. Run `schema.sql` in the project SQL editor. See "Running migrations in the
   SQL editor" below first — the editor silently discards scripts that carry
   their own `begin;` / `commit;`.
4. Run `digitization_workspace.sql` to enable authenticated private job uploads.
5. Copy the project URL and anonymous key into `cof_database/.env`:

   ```env
   VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
   VITE_SUPABASE_ANON_KEY=YOUR_ANON_KEY
   ```

5. Upload assets into the public `pxrd-assets` bucket. Store the object paths,
   not complete URLs, in `crop_path`, `digitized_plot_path`, `overlay_path`, and
   `data_path`.

Recommended object layout:

```text
pxrd-assets/
  papers/<paper-id>/figures/<figure-id>/source.png
  papers/<paper-id>/figures/<figure-id>/digitized.png
  papers/<paper-id>/figures/<figure-id>/overlay.png
  papers/<paper-id>/figures/<figure-id>/curves.csv
```

The public database surface is read-only. Never place a Supabase service-role
key in a `VITE_` variable or commit it to the repository. Curated writes should
happen through a trusted ingestion script or server process.

Authenticated users have a separate, private write surface:

```text
pxrd-user-uploads/<auth.uid()>/<job-id>/<random-name>.pdf
pxrd-user-results/<auth.uid()>/<job-id>/<result-name>
```

The public `pxrd_*` tables and `pxrd-assets` bucket remain read-only. User
uploads never become public records automatically. The authenticated browser
can use narrow RPCs to create a quota-controlled draft, register inputs, save
the job, and delete unprocessed private files; it cannot write worker estimates,
actual cost, results, or completion status. `saved` is deliberately not a queue
state. A future worker must produce a server quote and obtain explicit approval
before a job can become `queued`.

The frontend reads the lightweight `pxrd_paper_index` view on the dashboard and only
loads nested figure/curve metadata on a paper detail page. This avoids downloading
the complete curve inventory just to render paper counts.


## Running migrations in the SQL editor

**Strip `begin;` / `commit;` before pasting a migration into the Supabase SQL
editor.** The editor wraps whatever you submit in its own transaction. A script
that opens its own nested transaction can fail partway, roll back everything,
and still report `Success. No rows returned` — the error is swallowed and the
result is indistinguishable from a script that did nothing at all.

This is not hypothetical. Both files under `migrations/` were run this way on
2026-08-31 and reported success while landing nothing: not one column, and not
even the plain `UPDATE` that retires the false `'reviewed'` badge. The database,
the project ref, and the credentials were all correct.

How to tell that state apart from a stale PostgREST schema cache, which looks
similar from the client:

| Symptom | Meaning |
| --- | --- |
| `42703 column ... does not exist` | Postgres itself answering. The DDL is genuinely absent. |
| `PGRST205 ... in the schema cache` | Only PostgREST is behind. Reload the schema from the dashboard. |
| A `UPDATE` in the migration also had no effect | Never a cache problem. Caching cannot hide a committed row change. |

If a migration reports success but nothing lands, prove the editor persists at
all before re-running anything:

```sql
alter table public.pxrd_figures add column if not exists migration_probe text;
-- check for the column, then:
alter table public.pxrd_figures drop column if exists migration_probe;
```

If the probe lands, the connection and permissions are fine and the transaction
wrapper is the problem. Re-run the migration with `begin;`/`commit;` removed:
every statement in `migrations/` is individually guarded (`add column if not
exists`, `drop constraint if exists` before each `add constraint`), so running
them without an enclosing transaction is safe. A failure then surfaces as a real
error, and the statements that already succeeded stay applied.

The files in `migrations/` keep their `begin;`/`commit;` because that is correct
for `psql` and for any client that does not add its own. The wrapper only has to
come off for the dashboard editor.
