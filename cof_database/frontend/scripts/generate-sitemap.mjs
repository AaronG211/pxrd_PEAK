/**
 * Write public/sitemap.xml and public/robots.txt before the Vite build.
 *
 * Why this exists: the app is client-rendered, so the HTML a crawler receives
 * contains zero <a> elements, and pagination advances React state without
 * changing the URL. Measured against the live site, exactly 25 of 2,000 paper
 * pages were reachable by following links — the other 1,975 could not be
 * discovered at all, and a search for the site's own hostname returned nothing.
 * A sitemap is the one mechanism that does not depend on the link graph.
 *
 * Files under public/ bypass the SPA rewrite in vercel.json, which is why this
 * writes there rather than adding a route.
 *
 * Failure is soft on purpose. If Supabase is unreachable at build time this
 * emits a sitemap covering only the static routes and warns; shipping a smaller
 * sitemap is strictly better than failing a deploy over it.
 *
 * No <lastmod>. The updated_at column is not maintained (no trigger, and the
 * importer never sends it), so every value predates the row's own contents.
 * Publishing it as a modification date would be telling crawlers something the
 * database cannot support. Add it here once that column is fixed.
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, existsSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(here, "..", "public");
const SITE = process.env.SITE_ORIGIN?.replace(/\/$/, "") || "https://pxrd-peak.vercel.app";
const PAGE = 1000;

/** Vite reads the project-root .env (envDir: ".."); mirror that here. */
function readEnv() {
  const env = { ...process.env };
  for (const candidate of [resolve(here, "..", "..", ".env"), resolve(here, "..", ".env")]) {
    if (!existsSync(candidate)) continue;
    for (const line of readFileSync(candidate, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
      const index = trimmed.indexOf("=");
      const key = trimmed.slice(0, index).trim();
      const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, "");
      if (key && env[key] === undefined) env[key] = value;
    }
  }
  return env;
}

async function fetchPaperIds(url, key) {
  const ids = [];
  for (let from = 0; ; from += PAGE) {
    const response = await fetch(
      `${url}/rest/v1/pxrd_paper_index?select=id&order=paper_number` +
        `&limit=${PAGE}&offset=${from}`,
      { headers: { apikey: key, Authorization: `Bearer ${key}` } },
    );
    if (!response.ok) throw new Error(`PostgREST ${response.status}: ${await response.text()}`);
    const rows = await response.json();
    ids.push(...rows.map((row) => row.id));
    if (rows.length < PAGE) break;
  }
  return ids;
}

const xmlEscape = (value) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function urlEntry(path, priority, changefreq) {
  return `  <url>\n    <loc>${xmlEscape(SITE + path)}</loc>\n` +
    `    <changefreq>${changefreq}</changefreq>\n` +
    `    <priority>${priority}</priority>\n  </url>`;
}

const env = readEnv();
const url = env.VITE_SUPABASE_URL?.trim();
const key = env.VITE_SUPABASE_ANON_KEY?.trim();

let paperIds = [];
if (url && key) {
  try {
    paperIds = await fetchPaperIds(url, key);
  } catch (cause) {
    console.warn(`[sitemap] Supabase unreachable, emitting static routes only: ${cause}`);
  }
} else {
  console.warn("[sitemap] No Supabase credentials; emitting static routes only.");
}

const entries = [
  urlEntry("/", "1.0", "daily"),
  urlEntry("/digitize", "0.3", "monthly"),
  urlEntry("/docs/api", "0.5", "monthly"),
  // Paper pages are the corpus. They are the reason the sitemap exists.
  ...paperIds.map((id) => urlEntry(`/paper/${encodeURIComponent(id)}`, "0.8", "monthly")),
];

mkdirSync(publicDir, { recursive: true });
writeFileSync(
  resolve(publicDir, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    entries.join("\n") +
    `\n</urlset>\n`,
  "utf8",
);

writeFileSync(
  resolve(publicDir, "robots.txt"),
  [
    "# Open PXRD Database",
    "User-agent: *",
    "Allow: /",
    "",
    "# The auth callback carries a one-time code and has nothing to index.",
    "Disallow: /auth/",
    "",
    `Sitemap: ${SITE}/sitemap.xml`,
    "",
  ].join("\n"),
  "utf8",
);

console.log(
  `[sitemap] ${entries.length} URLs (${paperIds.length} papers) -> public/sitemap.xml, public/robots.txt`,
);
