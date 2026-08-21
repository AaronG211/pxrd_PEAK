# Supabase setup

1. Create a Supabase project.
2. For the approved legacy reset, run `reset.sql` once after creating a backup.
3. Run `schema.sql` in the project SQL editor.
4. Copy the project URL and anonymous key into `frontend/.env.local`:

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

The browser has read-only access. Never place a Supabase service-role key in a
`VITE_` variable or commit it to the repository. Writes should happen through a
trusted ingestion script or server process.

The frontend reads the lightweight `pxrd_paper_index` view on the dashboard and only
loads nested figure/curve metadata on a paper detail page. This avoids downloading
the complete curve inventory just to render paper counts.
