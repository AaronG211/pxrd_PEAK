# Open PXRD frontend

React/Vite interface for browsing paper-linked PXRD figures and digitized data.

The Vite config reads the project-root `.env`. Only variables prefixed with
`VITE_` are exposed to browser code; `SUPABASE_SECRET_KEY` remains server-side.

```bash
npm install
npm run dev
```

When Supabase variables are absent, the app displays a three-paper demo snapshot
built from the local PXRD database. The same variables also initialize Supabase
Auth; the Google client secret remains in the Supabase Dashboard and is never a
frontend environment variable.

The Vite config uses `envDir: ".."`, so local values are read from
`cof_database/.env`:

```env
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=YOUR_PUBLISHABLE_OR_ANON_KEY
```

Run `../supabase/digitization_workspace.sql` in the Supabase SQL editor before
testing private PDF uploads. Saving a job uploads the files and records the
client estimate, but intentionally stops at `saved`; it is not a processing
queue entry, makes no model request, and does not collect an API key.
