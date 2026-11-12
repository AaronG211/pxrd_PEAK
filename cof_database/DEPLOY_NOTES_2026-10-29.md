# Deploy notes: 155 curves on axis-contradicted figures leave `clean` (2026-10-29)

Nothing below has been run. Each step changes something public, so each one is
the author's to do or approve.

## What changed locally

- `outputs/pxrd.db` (research checkout): `tools/relabel_contradicted_axes.py --apply`
  moved the 155 clean curves on the 124 figures of paper SI Section S3.5 to
  `admission = 'excluded'`, `admission_reason = 'axis_contradicted'`,
  `in_clean_set = 0`. Change record, with every old value:
  `outputs/analysis/si/arb_relabel_2026-10-29.json`. Undo: `--revert`.
- `scripts/site_admission.py`: `('excluded', 'axis_contradicted') -> 'axis_unconfirmed'`.
- `scripts/import_pxrd_to_supabase.py`: accession-number cohorts 1 and 2 now read
  the clean set as it stood when they were numbered. Without this, a re-import
  would have renumbered 2,296 of the 2,370 papers (PXRD-xxxxx), because 36
  papers have no clean curve left. Checked: the fixed code gives the same order
  on the relabelled database as the old code on the unchanged one.
- `supabase/migrations/20261029_axis_contradicted_out_of_clean.sql`: the same
  change for the live table, without a re-import.

Counts after: clean 7,713 -> 7,558; axis_unconfirmed 517 -> 672; axis_verified
1,672, shape_flagged 1,339 and model_derived 204 unchanged; 11,445 curves,
4,664 figures and 2,370 papers unchanged.

## Steps (in this order)

Superseded on 2026-11-11: run these steps in the combined order of the last
section, "Remove images taken from source articles".

1. **Supabase SQL editor.** Paste
   `supabase/migrations/20261029_axis_contradicted_out_of_clean.sql` with its
   `begin;` / `commit;` lines removed. Its last SELECT must show `relabelled 155`,
   `still_clean 0`, then clean 7558 and axis_unconfirmed 672. "Success" alone
   proves nothing (see README, "Running migrations in the SQL editor").
2. **Optional re-import** (T7 attached): `scripts/import_pxrd_to_supabase.py
   --limit 100000` (the `--upload-overlays` flag no longer exists, and the
   import now needs the 2026-11-11 migration first). Step 1 already sets the curve rows; a
   re-import only matters if anything else was changed locally. It must run
   with the cohort fix above, or paper numbers move. It also clears the
   crystallinity descriptors of the 155 curves (`not_computed`), as for every
   other non-clean class; step 1 alone leaves their old values in place.
3. **Merge this branch into `main` and push `main`.** Vercel rebuilds the browse
   snapshot (`public/data/paper-index.json`) and the sitemap at deploy time, so
   the clean-only browse option is stale until this push. Then run
   `npm run test:api` and check `/api/v1/curves?admission=clean` counts.
   If the snapshot is missing after the deploy, the database was cold: warm it
   and push an empty commit.
4. **Data release.** Regenerate the export
   (`scripts/export_dataset.py --db ../outputs/pxrd.db --assets-root ../outputs/full-run-01 --out <EXPORT> --scope published`),
   attach it to a new GitHub release, and publish a new version of the Zenodo
   data record (concept DOI 10.5281/zenodo.23539380; 23539381 is the
   2026-10-21 version). The Publish click on
   Zenodo is the author's. In the published scope, `curves.csv.gz` has no
   `admission_reason` column (checked 2026-11-11): the 155 curves show
   `admission = axis_unconfirmed` and `in_clean_set = 0`, and they are the
   `axis_unconfirmed` curves whose figure has no flagged curve. Only
   `--scope accepted` writes `admission_reason = axis_contradicted`; do not
   attach that export. The exporter writes no `README.md` and no
   `SHA256SUMS.txt`: add both, with the checksums made from the exact files
   that are uploaded.
`README.md` and the comments in `frontend/src/types.ts` already carry the new
counts. `frontend/src/pages/HomePage.tsx` mentions 7,713 only in comments about
the 2026-10-21 change, which stay true as history.

## Rename to COF-PEAK (2026-11-02)

"Open PXRD Database" is too close to opXRD (Open Experimental Powder X-ray
Diffraction Database, 2025); the PI flagged it and the author chose COF-PEAK.
The URL (pxrd-peak.vercel.app) and the repository name (pxrd_PEAK) stay.
**Live since 2026-11-02: the rename alone was pushed to main (99e1fd4c; after
the 2026-11-12 history cleanup below it is c9547408).** This branch carries
the same change (now 4e0c80c8), so merging it later changes no name. The site code is renamed (title, header, footer, citation, API
name and docs, sitemap header, export file name, READMEs, `.zenodo.json`).
Applied migrations keep the old name in their comments, as history; the
internal sessionStorage key `open-pxrd-auth-return-to` is unchanged so that a
login in progress is not broken.

When publishing (steps 3 and 4), also (since 2026-11-11 the site cites no
Zenodo record; the two Zenodo items below concern the paper only, see the
end of the last section):
- Zenodo data record, new version: title
  "COF-PEAK: powder X-ray diffraction curves digitized from figures in the
  covalent organic framework literature" (the paper's reference 34 cites this
  title; drop "(release 2026-10-21)").
- Zenodo code record: the GitHub integration takes the title from
  `.zenodo.json`, "pxrd_PEAK: extraction pipeline and website for the COF-PEAK
  database" (the paper's reference 35).
- GitHub repository description and the release notes, if they name the site.

## Remove images taken from source articles (2026-11-11)

Decision: the site shows, serves and stores no image taken from another
author's figure, CC BY articles included. Our replot of the curve data and the
CSV downloads stay. Nothing below has been run.

What this branch changes:
- Paper page: no source-crop tile and no trace-overlay tile. A figure card
  keeps its label, page, caption, calibration checks, the interactive replot,
  the curve inventory and the downloads.
- Homepage and demo: the three `frontend/public/demo/*-source.png` crops are
  deleted, and so are the three `*-digitized.png` files, which nothing used.
  Wording that promised a source crop beside each trace is rewritten.
- Citation and license: the dataset is cited by
  https://github.com/AaronG211/pxrd_PEAK/releases and the code by the
  repository; the site and the API name no Zenodo record. The "not covered by
  either license" sentence now names quoted captions only (site, API and
  `LICENSE`).
- API: it never returned an image URL. `bulk_download` and `citation.dataset`
  now name the GitHub releases page, `citation.code` the repository.
  `scripts/smoke-api.mjs` also checks that the index names no Zenodo record and
  that no response it reads (index, papers, curves, figures, materials) carries
  an image field or image URL.
- Importer: uploads only `curves.csv.gz` and writes no image path;
  `--upload-overlays`, `--trim` and `--trim-top` are gone, and
  `scripts/figure_trim.py` is deleted. A full run first checks that
  `pxrd_figures.crop_path` is gone and refuses, before any upload or upsert,
  while it is not.
- `supabase/migrations/20261111_remove_publisher_images.sql` drops
  `pxrd_figures.crop_path`, `digitized_plot_path` and `overlay_path`. It also
  makes `pxrd-assets` accept `application/gzip` uploads only, so an older
  importer cannot put the crops back, and removes `image/png` and `image/webp`
  from the private `pxrd-user-results` bucket, to which no worker writes yet.
  `schema.sql` and `digitization_workspace.sql` carry the same settings.
- `scripts/delete_publisher_images.py` deletes the publisher-derived objects
  in `pxrd-assets`. Dry run of 2026-11-11 (listing only, public key): 18,656
  objects; 9,328 to delete (4,664 `source.webp` and 4,664 `overlay.webp`,
  240.7 MiB); kept: 4,664 `digitized.webp` and 4,664 `curves.csv.gz`;
  0 unclassified.

### Deploy order (this change together with the pending 2026-10-29 steps)

Run the importer and the cleanup script only from the checkout that holds
this branch. The untracked `cof_database/` copy in the research checkout
(`pxrd_fetcher`) carries the August importer, which uploads `source.webp`
before it writes any table, and the three demo crops. Deleting or archiving
that copy is the author's call.

1. **SQL editor: `20261029_axis_contradicted_out_of_clean.sql`** (2026-10-29
   step 1, unchanged). Strip `begin;` / `commit;`. Last SELECT: `relabelled
   155`, `still_clean 0`, clean 7558, axis_unconfirmed 672.
2. **Commit, merge into `main`, push `main`** (2026-10-29 step 3).
   - Every change of this work is still uncommitted on
     `feature/crossref-and-curve-plot`. Commit it first; the commit and the
     push are the author's to approve.
   - The merge also brings b7b68aa4 (the 2026-10-29 relabel: admission
     mapping, frozen accession cohorts, migration draft) and 4e0c80c8, which
     makes the same rename as c9547408, already on `main`.
   - Publish the data release first (author's click). On 2026-11-11
     `data-2026-10-21` was still a draft: the public releases page shows only
     `data-2026-10-01` (9,385 curves), and the download URLs on the API docs
     page return 404. With Zenodo gone, the site's dataset citation leads
     there. The draft's notes still say "Open PXRD" and name the Zenodo DOI.
     If step 7 makes a newer release instead, publish that one and set
     `RELEASE_TAG` before this push.

   Wait until Vercel shows the deploy as Ready. Then check that the live bundle
   no longer reads the image columns:

   ```bash
   for js in $(curl -s https://pxrd-peak.vercel.app/ | grep -o '/assets/[^"]*\.js'); do
     curl -s "https://pxrd-peak.vercel.app$js" | grep -c crop_path
   done   # every line must print 0
   ```

   Open one paper page: no image tiles; the plot and "Download all curves"
   work. If the browse snapshot is missing, the database was cold: warm it and
   push an empty commit.
3. **`npm run test:api`** in `frontend/`. It must pass, new checks included.
   Check the `/api/v1/curves?admission=clean` count as in 2026-10-29 step 3.
4. **SQL editor: `20261111_remove_publisher_images.sql`**, pasted as is (it has
   no `begin;` / `commit;`). Last SELECT: `image_columns_left 0`,
   `figures 4664`, `asset_mime_types {application/gzip}`, and
   `results_mime_types` without `image/png` or `image/webp`. Never before
   step 2: the old frontend selects `crop_path` and `overlay_path`, so every
   paper page would fail with 42703. Afterwards run `npm run test:api` again
   and open one paper page.

   From here on, do not roll the production deployment back (Vercel Instant
   Rollback) to a build from before this change: its paper pages would fail
   with 42703.
5. **Storage cleanup**, from `cof_database/`:
   `python3 scripts/delete_publisher_images.py` (dry run). It must show 9,328
   `publisher_derived` and 0 `unclassified`; if a new name appears, stop and
   look at it. With the secret key it also lists the buckets; a warning about
   another public bucket means that bucket has to be looked at too. Then
   `python3 scripts/delete_publisher_images.py --apply`. It lists the bucket
   again and exits 0 only when no publisher-derived object is left. Never
   before step 2: the old page would show broken tiles. Afterwards take one
   path from the dry-run listing and check the public URL:

   ```bash
   curl -sI "$SUPABASE_URL/storage/v1/object/public/pxrd-assets/papers/<paper-id>/<figure-id>/source.webp" | head -1
   # must be 400 or 404, not 200
   ```
6. **Optional re-import** (2026-10-29 step 2, T7 attached):
   `python3 -u scripts/import_pxrd_to_supabase.py --limit 100000 --workers 8`,
   without `--upload-overlays` (argparse now rejects it). Only after step 4;
   before it the importer refuses and writes nothing. The browse snapshot and
   the sitemap are built at deploy time, so afterwards push an empty commit to
   `main` (or redeploy in Vercel), wait for Ready, and run `npm run test:api`
   again.
7. **Data release** (2026-10-29 step 4): export and attach to a new GitHub
   release as described there, and publish it. If the release has a new tag,
   set `RELEASE_TAG` in `frontend/src/pages/ApiDocsPage.tsx` and push `main`
   again. The site no longer depends on Zenodo. Whether to publish new Zenodo
   versions (the "When publishing" list above) is the author's decision for
   the paper, whose references 34 and 35 cite the Zenodo records.

Not changed here, for the author:
- `.zenodo.json` at the repository root still says the dataset is archived as
  a separate Zenodo record, and the paper's references 34 and 35 cite the
  Zenodo records, while the site now cites the GitHub releases. Either move
  `.zenodo.json` and the paper to the GitHub release, or keep Zenodo for the
  paper and leave the "When publishing" list as it is.
- Earlier Vercel deployments keep their own URLs and still serve the three
  `/demo/*-source.png` crops and the old paper page until they are deleted in
  the Vercel dashboard.
- History cleanup, 2026-11-12 (author's decision): the three
  `public/demo/*-source.png` crops were removed from every commit of `main`
  and from the tags `data-2026-10-01` and `data-2026-10-21` (git filter-branch,
  index filter on those three paths only), and the remote branches
  `codex/open-pxrd-site` and `codex/pxrd-discovery-ux` were deleted. All 33
  commits were compared old against new: no other file, author, date, message
  or parent changed; the three commits made before the crops were added (from
  the first commit c8118717) keep their hashes. main moved 99e1fd4c ->
  c9547408; this branch was rebased onto it with an identical tree. Backup of
  the full pre-cleanup repository: ~/Desktop/pxrd_PEAK_backup_before_history_cleanup_2026-11-11/
  and the same folder under /Volumes/T7/pxrd_fetcher_data/backups/. GitHub may
  still show the old commits by hash for a while; a full purge of its caches
  needs GitHub Support.
- Search engines may keep old image URLs for a while; once step 5 has run,
  those URLs no longer serve the image.
