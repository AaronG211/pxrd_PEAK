import type { ReactNode } from "react";
import { usePageMeta } from "../hooks/usePageMeta";

/**
 * Documentation for /api/v1 (api/v1.ts).
 *
 * The examples here are the contract a reader will copy into a notebook, so
 * each one was run against the deployed API before it was written down. If an
 * endpoint or field changes, change it here in the same commit — a docs page
 * that drifts from the API is worse than none, because it is believed.
 */

const BASE = "https://pxrd-peak.vercel.app/api/v1";

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-slate-200 bg-slate-900 p-4 text-[13px] leading-relaxed text-slate-100">
      <code>{children}</code>
    </pre>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 border-t border-slate-200 pt-8">
      <h2 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h2>
      <div className="mt-4 space-y-4 text-sm leading-relaxed text-slate-700">{children}</div>
    </section>
  );
}

const ENDPOINTS: Array<[string, string]> = [
  ["/stats", "Corpus counts."],
  ["/papers", "List and search papers. Paginated."],
  ["/papers/{paper_id}", "One paper, with its figures and their curves."],
  ["/curves", "List and filter curves across the corpus. Paginated."],
  ["/curves/{series_id}", "One curve, including its automatic (unvetted) peak list."],
  ["/curves/{series_id}/data", "The 2θ / intensity points of one curve. ?format=json (default) or csv."],
  ["/figures/{figure_id}", "One figure, with its curves and axis-calibration details."],
  ["/figures/{figure_id}/data", "Every curve in one figure as gzipped CSV (redirects to the file)."],
];

const CURVE_PARAMS: Array<[string, string]> = [
  ["paper_id, figure_id", "Restrict to one paper or one figure."],
  ["material", "Substring of the material name as printed in the paper, case-insensitive."],
  ["first_peak_min, first_peak_max", "Window on the first peak position, degrees 2θ."],
  ["first_peak_status", "Comma-separated subset of ok, low_confidence, truncated_at_window_start, no_bragg_peak."],
  ["stacking_hump_status", "hump_detected, no_hump_detected, window_not_covered or not_computed."],
  ["axis_verification", "axis_cross_validated, axis_single_method or axis_arbitrated."],
  ["role", "experimental, simulated, refined, difference, reference or unclassified."],
  ["admission", "clean or axis_verified (see below)."],
  ["sort", "series_id (default), first_peak or -first_peak."],
  ["limit, offset", "Page size 1–1000 (default 100) and start position."],
];

const PAPER_PARAMS: Array<[string, string]> = [
  ["q", "Substring of the title, DOI or author list."],
  ["year, year_min, year_max", "Publication year."],
  ["journal", "Substring of the journal name."],
  ["publication_status", "active, corrected, retracted, withdrawn or concern."],
  ["include_readmitted", "Default true. false keeps only papers with at least one clean curve."],
  ["sort", "paper_number (default), year, -year or -curves."],
  ["limit, offset", "Page size 1–1000 (default 100) and start position."],
];

function ParamTable({ rows }: { rows: Array<[string, string]> }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="min-w-full text-left text-sm">
        <tbody className="divide-y divide-slate-100">
          {rows.map(([name, desc]) => (
            <tr key={name}>
              <td className="whitespace-nowrap px-4 py-2.5 align-top font-mono text-xs text-slate-900">{name}</td>
              <td className="px-4 py-2.5 text-slate-600">{desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ApiDocsPage() {
  usePageMeta({
    title: "API",
    description:
      "Read-only JSON API for the Open PXRD Database: search papers and curves, filter by first-peak position, and download digitized 2-theta / intensity data. No key required.",
    canonical: "/docs/api",
  });

  return (
    <div className="section-container max-w-4xl py-10">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">API</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
        Read-only JSON over HTTPS. No key, no sign-up. Everything the website shows is
        available here, plus the per-curve data in machine-readable form.
      </p>
      <p className="mt-4 inline-block rounded-md border border-slate-200 bg-white px-3 py-1.5 font-mono text-sm text-slate-900">
        {BASE}
      </p>

      <div className="mt-10 space-y-10">
        <Section id="quick-start" title="Quick start">
          <p>Curves whose first peak lies between 3.0° and 3.2° 2θ:</p>
          <Code>{`curl "${BASE}/curves?first_peak_min=3.0&first_peak_max=3.2&first_peak_status=ok&limit=5"`}</Code>
          <p>The same in Python, then the points of the first match:</p>
          <Code>{`import requests

BASE = "${BASE}"

r = requests.get(f"{BASE}/curves", params={
    "first_peak_min": 3.0,
    "first_peak_max": 3.2,
    "first_peak_status": "ok",
    "limit": 100,
})
r.raise_for_status()
curves = r.json()["data"]

points = requests.get(curves[0]["links"]["data"]).json()
two_theta = points["two_theta_deg"]          # list of floats
intensity = points["relative_intensity"]     # same length`}</Code>
          <p>Straight into pandas:</p>
          <Code>{`import pandas as pd

df = pd.read_csv(f"{BASE}/curves/{curves[0]['series_id']}/data?format=csv")`}</Code>
        </Section>

        <Section id="endpoints" title="Endpoints">
          <p>All paths are relative to the base URL above.</p>
          <ParamTable rows={ENDPOINTS} />
          <p>
            <span className="font-mono text-xs">{BASE}</span> itself returns a machine-readable
            index of these endpoints.
          </p>
        </Section>

        <Section id="curves" title="Filtering curves">
          <ParamTable rows={CURVE_PARAMS} />
        </Section>

        <Section id="papers" title="Searching papers">
          <ParamTable rows={PAPER_PARAMS} />
        </Section>

        <Section id="pagination" title="Pagination">
          <p>
            List endpoints return <span className="font-mono text-xs">{"{ data, meta }"}</span>.{" "}
            <span className="font-mono text-xs">meta.total</span> is the full match count and{" "}
            <span className="font-mono text-xs">meta.next</span> is the URL of the next page, or{" "}
            <span className="font-mono text-xs">null</span> on the last one. Follow it until it
            runs out:
          </p>
          <Code>{`def every(url, **params):
    params.setdefault("limit", 1000)
    while url:
        page = requests.get(url, params=params).json()
        yield from page["data"]
        url, params = page["meta"]["next"], None   # next already carries the query

all_ok_curves = list(every(f"{BASE}/curves", first_peak_status="ok"))`}</Code>
        </Section>

        <Section id="reading" title="Reading the data correctly">
          <ul className="list-disc space-y-2.5 pl-5">
            <li>
              <strong className="font-semibold text-slate-900">Every curve is digitized from a published figure</strong>,
              not read from a diffractometer file. Peak positions are the reliable part;{" "}
              <span className="font-mono text-xs">two_theta_uncertainty_deg</span> is the per-curve axis error
              (median ≈ 0.05°). Peak shapes and heights inherit the figure&rsquo;s resolution and any smoothing
              the authors applied.
            </li>
            <li>
              <span className="font-mono text-xs">relative_intensity</span> is scaled per curve so the tallest
              feature is roughly 100. It is <strong className="font-semibold text-slate-900">not comparable across curves</strong>.
            </li>
            <li>
              <span className="font-mono text-xs">first_peak.d_spacing_angstrom_derived</span> is computed from 2θ at{" "}
              <span className="font-mono text-xs">first_peak.wavelength_angstrom</span>. Unless{" "}
              <span className="font-mono text-xs">wavelength_source</span> is{" "}
              <span className="font-mono text-xs">paper_reported</span>, that wavelength is an assumed Cu Kα, so the
              d-spacing carries no information the angle does not.
            </li>
            <li>
              <span className="font-mono text-xs">crystallinity.sharp_feature_fraction</span> is the share of intensity in
              features narrower than ~4°. It is not a degree of crystallinity and depends strongly on how much of the
              pattern was plotted. <span className="font-mono text-xs">first_peak_to_hump_ratio</span> is a height ratio
              within one curve; no Miller indices were assigned.
            </li>
            <li>
              <span className="font-mono text-xs">unvetted_peaks</span> is the raw automatic peak list. It passes none of
              the checks behind <span className="font-mono text-xs">first_peak.status</span>.
            </li>
            <li>
              <span className="font-mono text-xs">admission</span> is <span className="font-mono text-xs">clean</span> when
              the curve and every other curve in its figure passed the automated shape checks, and{" "}
              <span className="font-mono text-xs">axis_verified</span> when the curve passed but a sibling did not, in a
              figure whose 2θ axis was fit two independent ways that agreed.{" "}
              <span className="font-mono text-xs">axis_verification</span> says how the figure&rsquo;s axis was established.
            </li>
            <li>
              <strong className="font-semibold text-slate-900">Training a model? Split by paper_id</strong>, never by curve.
              Curves in one figure usually share a material and always share an axis; a random curve-level split puts
              near-duplicates on both sides.
            </li>
          </ul>
        </Section>

        <Section id="conventions" title="Conventions">
          <ul className="list-disc space-y-2.5 pl-5">
            <li>
              <strong className="font-semibold text-slate-900">Stable within v1.</strong> Fields will not be renamed or
              removed under <span className="font-mono text-xs">/api/v1</span>; new fields may be added. A breaking
              change ships as a new version prefix.
            </li>
            <li>
              Errors return <span className="font-mono text-xs">{"{ error: { code, message } }"}</span> with a matching
              HTTP status. Unknown query parameters are rejected with a 400 that lists the valid ones, so a typo never
              silently returns the default page.
            </li>
            <li>Responses are cached for up to an hour. The data changes only when the corpus is re-imported.</li>
            <li>CORS is open, so the API can be called from a browser or a hosted notebook.</li>
            <li>
              Please be considerate with large crawls. For the whole corpus as one archive, open an issue on{" "}
              <a
                href="https://github.com/AaronG211/pxrd_PEAK"
                target="_blank"
                rel="noreferrer"
                className="font-medium text-blue-700 hover:underline"
              >
                GitHub
              </a>
              .
            </li>
          </ul>
        </Section>
      </div>
    </div>
  );
}
