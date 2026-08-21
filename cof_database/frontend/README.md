# Open PXRD frontend

React/Vite interface for browsing paper-linked PXRD figures and digitized data.

The Vite config reads the project-root `.env`. Only variables prefixed with
`VITE_` are exposed to browser code; `SUPABASE_SECRET_KEY` remains server-side.

```bash
npm install
npm run dev
```

When Supabase variables are absent, the app displays a three-paper demo snapshot
built from the local PXRD database.
