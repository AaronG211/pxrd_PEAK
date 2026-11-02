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

1. **Supabase SQL editor.** Paste
   `supabase/migrations/20261029_axis_contradicted_out_of_clean.sql` with its
   `begin;` / `commit;` lines removed. Its last SELECT must show `relabelled 155`,
   `still_clean 0`, then clean 7558 and axis_unconfirmed 672. "Success" alone
   proves nothing (see README, "Running migrations in the SQL editor").
2. **Optional re-import** (T7 attached): `scripts/import_pxrd_to_supabase.py
   --limit 100000 --upload-overlays`. Step 1 already sets the curve rows; a
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
   Zenodo is the author's. `curves.csv` will show 155 curves with
   `admission = axis_unconfirmed`, `admission_reason = axis_contradicted`.
`README.md` and the comments in `frontend/src/types.ts` already carry the new
counts. `frontend/src/pages/HomePage.tsx` mentions 7,713 only in comments about
the 2026-10-21 change, which stay true as history.

## Rename to COF-PEAK (2026-11-02)

"Open PXRD Database" is too close to opXRD (Open Experimental Powder X-ray
Diffraction Database, 2025); the PI flagged it and the author chose COF-PEAK.
The URL (pxrd-peak.vercel.app) and the repository name (pxrd_PEAK) stay.
**Live since 2026-11-02: the rename alone was pushed to main as 99e1fd4c
(deployed in about 45 s; browse snapshot rebuilt; `npm run test:api` 84/84).**
This branch carries the same change as 186bcad8, so merging it later changes
no name. The site code is renamed (title, header, footer, citation, API
name and docs, sitemap header, export file name, READMEs, `.zenodo.json`).
Applied migrations keep the old name in their comments, as history; the
internal sessionStorage key `open-pxrd-auth-return-to` is unchanged so that a
login in progress is not broken.

When publishing (steps 3 and 4), also:
- Zenodo data record, new version: title
  "COF-PEAK: powder X-ray diffraction curves digitized from figures in the
  covalent organic framework literature" (the paper's reference 34 cites this
  title; drop "(release 2026-10-21)").
- Zenodo code record: the GitHub integration takes the title from
  `.zenodo.json`, "pxrd_PEAK: extraction pipeline and website for the COF-PEAK
  database" (the paper's reference 35).
- GitHub repository description and the release notes, if they name the site.
