# Supabase setup

1. Create a Supabase project.
2. For the approved legacy reset, run `reset.sql` once after creating a backup.
3. Run `schema.sql` in the project SQL editor.
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
