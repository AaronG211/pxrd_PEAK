# Open PXRD Database

This project is the public interface for a traceable database of digitized
powder X-ray diffraction (PXRD) data. It is paper-centric: a researcher starts
from a publication, sees every indexed PXRD figure, compares the published crop
with reconstructed traces, and downloads the curve data.

## Current scope

- Paper index with paper number, title, DOI, journal, and year
- Search across paper metadata
- Side-by-side source crop and digitized plot
- Per-figure curve inventory and CSV download
- Supabase-ready public read model
- Local demo snapshot containing three real records from `pxrd_fetcher`

The previous AI extraction screens are no longer part of the public product.
Legacy backend code remains in place for now so research tooling is not removed
destructively while the database interface is being rebuilt.

## Architecture

```text
React/Vite website
        |
        | anonymous, read-only access
        v
Supabase Postgres  -- paper, figure, and curve metadata
Supabase Storage   -- source crops, digitized plots, overlays, CSV/Parquet files
```

Dense curve points belong in Storage files rather than millions of Postgres
rows. This keeps browsing queries small while preserving full-resolution data
for download.

## Run locally

```bash
cd frontend
npm install
npm run dev
```

The demo works without credentials. For live data, run `supabase/schema.sql`
and add `VITE_SUPABASE_URL` plus `VITE_SUPABASE_ANON_KEY` to the project-root
`.env`. Vite only exposes variables with the `VITE_` prefix.

Do not expose a Supabase service-role key in the frontend. Curated writes and
bulk imports should run from a trusted local script or server environment.

## Import the deterministic Free-plan pilot

The importer selects complete papers from the clean set using a stable hash, so
rerunning the same limit always targets the same records. It converts source and
digitized images to lossless WebP, combines each figure's points into one
compressed CSV, skips existing objects, and upserts metadata.

```bash
# Inspect the first 10 papers without network writes
python3 scripts/import_pxrd_to_supabase.py --limit 10 --dry-run

# Smoke test
python3 -u scripts/import_pxrd_to_supabase.py --limit 10 --workers 4

# Free-plan pilot
python3 -u scripts/import_pxrd_to_supabase.py --limit 1000 --workers 8
```

The approved 1,000-paper pilot contains 1,866 figures and 3,815 clean curves.
The command is safe to resume after interruption because existing Storage
objects are detected and all metadata writes are idempotent upserts.

## Zero-API title enrichment

Most source PDFs contain reliable embedded title metadata even when the local
paper table does not. The backfill script reads that metadata locally; it makes
no network or model calls and records every proposed change before applying it.

```bash
# Audit only
python3 scripts/backfill_pdf_titles.py --limit 1000

# Update the local SQLite title field
python3 scripts/backfill_pdf_titles.py --limit 1000 --apply

# Synchronize only paper metadata to Supabase (no asset upload)
python3 scripts/import_pxrd_to_supabase.py --limit 1000 --metadata-only
```

The metadata-only importer reads only `SUPABASE_URL` and
`SUPABASE_SECRET_KEY`; it never loads or invokes a model-provider credential.
