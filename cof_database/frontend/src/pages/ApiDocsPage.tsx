import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { CitingAndLicense } from "../components/CitingAndLicense";
import { getCorpusCounts } from "../lib/api";
import { usePageMeta } from "../hooks/usePageMeta";

/**
 * Documentation for /api/v1 (api/v1.ts).
 *
 * The examples here are the contract a reader will copy into a notebook, so
 * every code block is run before it ships — against a local copy of the handler
 * first, then against the deployed API. If an endpoint or field changes, change
 * it here in the same commit: a docs page that drifts from the API is worse
 * than none, because it is believed.
 *
 * Shaped by watching a first-time reader use it (2026-10-01). What they hit:
 * `role=experimental` returned half the corpus because most curves are
 * `unclassified`, while `role=simulated` returned nothing at all; `TpPa1` found
 * zero curves because `material` is a literal substring; nothing said what
 * paper_id was or how to cite a curve; and the only way to get everything was
 * 9,385 requests — about an hour and a half sequentially — or an email.
 */

const BASE = "https://pxrd-peak.vercel.app/api/v1";
/**
 * The bulk release matching what this API serves. data-2026-10-01 held the
 * 9,385 curves the site published then; from 2026-10-21 the site publishes all
 * 11,445 with their admission class, and the release with that scope is this
 * tag (scripts/export_dataset.py --scope published).
 */
const RELEASE_TAG = "data-2026-10-21";
const RELEASE_PAGE = `https://github.com/AaronG211/pxrd_PEAK/releases/tag/${RELEASE_TAG}`;
const RELEASE_FILES = `https://github.com/AaronG211/pxrd_PEAK/releases/download/${RELEASE_TAG}`;

/** A real curve object, captured verbatim from /curves?material_group=tppa1 on 2026-10-01. */
const EXAMPLE_CURVE = "{\n  \"series_id\": \"10.1002_advs.202303872-p004-f01-s03\",\n  \"paper_id\": \"10.1002_advs.202303872\",\n  \"doi\": \"10.1002/advs.202303872\",\n  \"figure_id\": \"10.1002_advs.202303872-p004-f01\",\n  \"label\": \"TpPa-1\",\n  \"material_name\": \"TpPa-1\",\n  \"material_group\": \"tppa1\",\n  \"sample_state\": \"experimental\",\n  \"role\": \"experimental\",\n  \"admission\": \"clean\",\n  \"two_theta_min_deg\": 2.903,\n  \"two_theta_max_deg\": 40.003,\n  \"point_count\": 1856,\n  \"two_theta_uncertainty_deg\": 0.0467,\n  \"trace_confidence\": 0.9975,\n  \"axis_verification\": \"axis_single_method\",\n  \"first_peak\": {\n    \"two_theta_deg\": 4.7231,\n    \"d_spacing_angstrom_derived\": 18.694,\n    \"fwhm_deg\": 0.977,\n    \"status\": \"ok\",\n    \"wavelength_angstrom\": 1.5406,\n    \"wavelength_source\": \"assumed_cu_ka\"\n  },\n  \"crystallinity\": {\n    \"sharp_feature_fraction\": 0.5769,\n    \"stacking_hump_status\": \"hump_detected\",\n    \"stacking_hump_center_deg\": 26.683,\n    \"stacking_hump_fwhm_deg\": 5.22,\n    \"first_peak_to_hump_ratio\": 2.05,\n    \"descriptor_version\": \"v0\"\n  },\n  \"links\": {\n    \"self\": \"https://pxrd-peak.vercel.app/api/v1/curves/10.1002_advs.202303872-p004-f01-s03\",\n    \"data\": \"https://pxrd-peak.vercel.app/api/v1/curves/10.1002_advs.202303872-p004-f01-s03/data\",\n    \"figure\": \"https://pxrd-peak.vercel.app/api/v1/figures/10.1002_advs.202303872-p004-f01\",\n    \"page\": \"https://pxrd-peak.vercel.app/paper/10.1002_advs.202303872#10.1002_advs.202303872-p004-f01\",\n    \"publication\": \"https://doi.org/10.1002/advs.202303872\",\n    \"material_group\": \"https://pxrd-peak.vercel.app/api/v1/curves?material_group=tppa1\"\n  }\n}";

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-slate-200 bg-slate-900 p-4 text-[13px] leading-relaxed text-slate-100">
      <code>{children}</code>
    </pre>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-xs">{children}</span>;
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 border-t border-slate-200 pt-8">
      <h2 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h2>
      <div className="mt-4 space-y-4 text-sm leading-relaxed text-slate-700">{children}</div>
    </section>
  );
}

function Table({ rows }: { rows: Array<[string, ReactNode]> }) {
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

const ON_THIS_PAGE: Array<[string, string]> = [
  ["quick-start", "Quick start"],
  ["download", "Download everything"],
  ["endpoints", "Endpoints"],
  ["curve-object", "The curve object"],
  ["curves", "Filtering curves"],
  ["materials", "Materials"],
  ["papers", "Searching papers"],
  ["pagination", "Pagination"],
  ["reading", "Reading the data correctly"],
  ["citing", "Citing and license"],
  ["conventions", "Conventions"],
];

const ENDPOINTS: Array<[string, ReactNode]> = [
  ["/stats", "Corpus counts."],
  ["/papers", "List and search papers. Paginated."],
  ["/papers/{paper_id}", "One paper, with its figures and their curves."],
  ["/curves", "List and filter curves across the corpus. Paginated."],
  ["/curves/{series_id}", "One curve, including its automatic (unvetted) peak list."],
  ["/curves/{series_id}/data", "The 2θ / intensity points of one curve. ?format=json (default) or csv."],
  ["/figures/{figure_id}", "One figure, with its curves and axis-calibration details."],
  ["/figures/{figure_id}/data", "Every curve in one figure as one gzipped CSV (redirects to the file)."],
  ["/materials", "Material label groups: one spelling-insensitive entry per label used in more than one paper."],
];

const CURVE_FIELDS: Array<[string, ReactNode]> = [
  ["series_id", <>Stable id of the trace: <Mono>{"{figure_id}-s{NN}"}</Mono>.</>],
  ["paper_id", <>The paper&rsquo;s DOI with <Mono>/</Mono> replaced by <Mono>_</Mono>, so it can sit in a URL path.</>],
  ["doi", <>The source paper&rsquo;s DOI, which doi.org resolves. Cite the paper alongside the dataset; see <a className="font-medium text-blue-700 hover:underline" href="#citing">Citing and license</a>.</>],
  ["figure_id", <><Mono>{"{paper_id}-p{page}-f{n}"}</Mono>: the figure&rsquo;s page and position in the PDF.</>],
  ["label", "The trace's legend text in the figure."],
  ["material_name", "The material as the paper names it, not normalised."],
  ["material_group", <>The cross-paper label group, or <Mono>null</Mono>. See <a className="font-medium text-blue-700 hover:underline" href="#materials">Materials</a>.</>],
  ["sample_state", "As the paper describes it (as-synthesised, activated, after soaking…); often null."],
  ["role", <>The role the extraction recorded: <Mono>experimental</Mono> where the recorded sample state says so; <Mono>simulated</Mono>, <Mono>refined</Mono>, <Mono>difference</Mono> or <Mono>reference</Mono> where the legend label names one (e.g. Pawley, JCPDS); otherwise <Mono>unclassified</Mono>, meaning none was recorded — about half the corpus, and not a sign that the figure left the role unsaid.</>],
  ["admission", <>Which class the curve is in: <Mono>clean</Mono>, <Mono>axis_verified</Mono>, <Mono>shape_flagged</Mono>, <Mono>axis_unconfirmed</Mono> or <Mono>model_derived</Mono>. Every accepted curve is published with its class; see <a className="font-medium text-blue-700 hover:underline" href="#reading">Reading the data</a>.</>],
  ["two_theta_min_deg, two_theta_max_deg", "The 2θ window this trace was plotted over."],
  ["point_count", "Number of digitized points."],
  ["two_theta_uncertainty_deg", "Per-curve error of the 2θ axis fit (median about 0.05°)."],
  ["trace_confidence", "0–1, automated: how faithfully the extracted trace follows the printed line."],
  ["axis_verification", <>How the figure&rsquo;s 2θ axis was established: <Mono>axis_cross_validated</Mono> (tick-mark fit and label OCR agreed), <Mono>axis_single_method</Mono> or <Mono>axis_arbitrated</Mono>.</>],
  ["first_peak", <>The lowest-angle Bragg peak — <Mono>two_theta_deg</Mono>, <Mono>d_spacing_angstrom_derived</Mono>, <Mono>fwhm_deg</Mono>, <Mono>status</Mono>, <Mono>wavelength_*</Mono> — or <Mono>null</Mono> when none was found.</>],
  ["crystallinity", "Within-curve shape descriptors; see Reading the data."],
  ["links", <><Mono>data</Mono> (the points), <Mono>page</Mono> (this figure on the website), <Mono>publication</Mono> (doi.org), <Mono>figure</Mono>, <Mono>material_group</Mono>, <Mono>self</Mono>.</>],
];

const CURVE_PARAMS: Array<[string, ReactNode]> = [
  ["paper_id, figure_id", "Restrict to one paper or one figure."],
  ["material_group", <>A group id from <Mono>/materials</Mono>. Use this, not <Mono>material</Mono>, to find one material across spellings.</>],
  ["material", <>Literal, case-insensitive substring of <Mono>material_name</Mono>. <Mono>TpPa-1</Mono> also matches <Mono>Fe₃O₄@TpPa-1</Mono>, and <Mono>TpPa1</Mono> matches nothing.</>],
  ["first_peak_min, first_peak_max", "Window on the first peak position, degrees 2θ, inclusive."],
  ["first_peak_status", "Comma-separated subset of ok, low_confidence, truncated_at_window_start, no_bragg_peak."],
  ["stacking_hump_status", "hump_detected, no_hump_detected, window_not_covered or not_computed."],
  ["axis_verification", "axis_cross_validated, axis_single_method or axis_arbitrated."],
  ["role", <><strong className="font-semibold text-slate-900">Not a way to select measured patterns.</strong> About half the corpus is <Mono>unclassified</Mono>, so <Mono>role=experimental</Mono> silently drops it; and <Mono>simulated</Mono>, <Mono>refined</Mono> and <Mono>difference</Mono> find only some of the <Mono>model_derived</Mono> curves. To leave computed traces out, filter on <Mono>admission</Mono> instead.</>],
  ["admission", <>One class or a comma-separated subset of <Mono>clean</Mono>, <Mono>axis_verified</Mono>, <Mono>shape_flagged</Mono>, <Mono>axis_unconfirmed</Mono>, <Mono>model_derived</Mono>. Measured patterns only: <Mono>admission=clean,axis_verified,shape_flagged,axis_unconfirmed</Mono>. The strictest tier: <Mono>admission=clean</Mono>.</>],
  ["sort", "series_id (default), first_peak or -first_peak."],
  ["limit, offset", "Page size 1–1000 (default 100) and start position."],
];

const MATERIAL_PARAMS: Array<[string, ReactNode]> = [
  ["q", <>A material name in any spelling. Matched after the website&rsquo;s normalisation — case, hyphens, spaces, sub/superscripts and Greek letters fold away — so <Mono>TpPa1</Mono>, <Mono>Tp-Pa-1</Mono> and <Mono>TpPa-1</Mono> meet.</>],
  ["specificity", <><Mono>specific</Mono> (default), <Mono>generic</Mono>, <Mono>serial</Mono>, <Mono>paper_local</Mono> or <Mono>all</Mono>.</>],
  ["sort", "-papers (default), -curves or name."],
  ["limit, offset", "Page size 1–1000 (default 100) and start position."],
];

const PAPER_PARAMS: Array<[string, ReactNode]> = [
  ["q", "Substring of the title, DOI or author list."],
  ["year, year_min, year_max", "Publication year."],
  ["journal", "Substring of the journal name."],
  ["publication_status", "active, corrected, retracted, withdrawn or concern."],
  ["include_readmitted", "Default true. false keeps only papers with at least one clean curve, dropping papers whose every curve is in another class."],
  ["sort", "paper_number (default), year, -year or -curves."],
  ["limit, offset", "Page size 1–1000 (default 100) and start position."],
];

const link = "font-medium text-blue-700 hover:underline";

export function ApiDocsPage() {
  usePageMeta({
    title: "API",
    description:
      "Read-only JSON API for the Open PXRD Database: search papers, curves and materials, filter by first-peak position, and download digitized 2-theta / intensity data, one curve at a time or all at once. No key required.",
    canonical: "/docs/api",
  });

  const [counts, setCounts] = useState<{ papers: number; figures: number; curves: number } | null>(null);
  useEffect(() => {
    let active = true;
    getCorpusCounts().then((value) => {
      if (active) setCounts(value);
    });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="section-container max-w-4xl py-10">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">API</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
        Read-only JSON over HTTPS. No key, no sign-up.{" "}
        {counts ? (
          <>
            {counts.curves.toLocaleString()} powder X-ray diffraction patterns from{" "}
            {counts.figures.toLocaleString()} figures in {counts.papers.toLocaleString()} published
            papers
          </>
        ) : (
          <>Powder X-ray diffraction patterns from figures in published papers</>
        )}
        , mostly from the covalent organic framework (COF) literature — about three in four of the
        papers&rsquo; titles name a COF.
      </p>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
        A <strong className="font-semibold text-slate-800">paper</strong> has figures; a{" "}
        <strong className="font-semibold text-slate-800">figure</strong> has curves; a{" "}
        <strong className="font-semibold text-slate-800">curve</strong> is one trace digitized from that
        figure, with its 2θ / intensity points. Every curve the pipeline accepted is published, and each says
        which class it is in (<a href="#reading" className={link}>admission</a>). Traces the figure labels as
        simulated, refined or difference curves were filtered out during extraction; the few that slipped past are
        published as <Mono>model_derived</Mono>, so everything else is a measured pattern — as far as the
        figures&rsquo; own labels can tell.
      </p>
      <p className="mt-4 inline-block rounded-md border border-slate-200 bg-white px-3 py-1.5 font-mono text-sm text-slate-900">
        {BASE}
      </p>
      <nav aria-label="On this page" className="mt-5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {ON_THIS_PAGE.map(([id, label]) => (
          <a key={id} href={`#${id}`} className={link}>{label}</a>
        ))}
      </nav>

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

        <Section id="download" title="Download everything">
          <p>
            <strong className="font-semibold text-slate-900">Use the bulk files.</strong> The whole published
            corpus is attached to a GitHub release as three gzipped CSVs and a README —{" "}
            <a href={RELEASE_PAGE} target="_blank" rel="noreferrer" className={link}>{RELEASE_TAG}</a>. It is
            the same data this API serves, with the same field names where they overlap.
          </p>
          <Code>{`import pandas as pd

REL = "${RELEASE_FILES}"
curves = pd.read_csv(f"{REL}/curves.csv.gz")   # one row per curve
papers = pd.read_csv(f"{REL}/papers.csv.gz")   # one row per paper
points = pd.read_csv(f"{REL}/points.csv.gz")   # series_id, two_theta_deg, relative_intensity`}</Code>
          <p>
            <strong className="font-semibold text-slate-900">Through the API, fetch per figure, not per curve.</strong>{" "}
            Each figure&rsquo;s file holds all of its curves, so the corpus is{" "}
            {counts
              ? `${counts.figures.toLocaleString()} requests instead of ${counts.curves.toLocaleString()}`
              : "one request per figure instead of one per curve"}
            , and each one is a redirect to a static file. Measured on 2026-10-01 from a US connection, when the
            corpus was 4,275 figures: about 113 MB in 2.4 minutes with 16 parallel requests; the same data one curve
            at a time took about an hour and a half sequentially. Please keep to 8 parallel requests or fewer.
          </p>
          <Code>{`import io
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import requests

BASE = "${BASE}"

def every(url, **params):                       # see Pagination
    params.setdefault("limit", 1000)
    while url:
        page = requests.get(url, params=params).json()
        yield from page["data"]
        url, params = page["meta"]["next"], None

curves = pd.DataFrame(every(f"{BASE}/curves"))

def figure_points(figure_id):
    r = requests.get(f"{BASE}/figures/{figure_id}/data")   # redirects to a .csv.gz
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content), compression="gzip")

with ThreadPoolExecutor(8) as pool:
    points = pd.concat(pool.map(figure_points, curves["figure_id"].unique()))`}</Code>
          <p>
            A figure file&rsquo;s columns are <Mono>series_id, series_label, material_name, sample_state,
            two_theta_deg, relative_intensity, two_theta_uncertainty_deg</Mono>. Read it with a CSV parser rather than
            splitting lines: text fields are quoted and may contain commas.
          </p>
        </Section>

        <Section id="endpoints" title="Endpoints">
          <p>All paths are relative to the base URL above.</p>
          <Table rows={ENDPOINTS} />
          <p>
            <Mono>{BASE}</Mono> itself returns a machine-readable index of these endpoints.
          </p>
        </Section>

        <Section id="curve-object" title="The curve object">
          <p>
            What <Mono>/curves</Mono> returns in <Mono>data</Mono>, and what <Mono>/curves/{"{series_id}"}</Mono>{" "}
            returns on its own (plus <Mono>unvetted_peaks</Mono>).
          </p>
          <details className="rounded-lg border border-slate-200 bg-white">
            <summary className="cursor-pointer px-4 py-2.5 text-sm font-medium text-slate-800">
              Example
            </summary>
            <div className="border-t border-slate-200 p-3">
              <Code>{EXAMPLE_CURVE}</Code>
            </div>
          </details>
          <Table rows={CURVE_FIELDS} />
        </Section>

        <Section id="curves" title="Filtering curves">
          <Table rows={CURVE_PARAMS} />
        </Section>

        <Section id="materials" title="Materials">
          <p>
            Papers spell the same label differently. <Mono>/materials</Mono> groups every label that appears in
            more than one paper under one normalised key, and <Mono>meta.exact_match</Mono> is the group whose key
            equals your query, if there is one:
          </p>
          <Code>{`curl "${BASE}/materials?q=TpPa1"
# meta.exact_match: {"id": "tppa1", "name": "TpPa-1", "label_variants": ["Tp-Pa-1", "TpPa-1"], ...}

curl "${BASE}/curves?material_group=tppa1"     # every curve labelled TpPa-1, in any spelling`}</Code>
          <Table rows={MATERIAL_PARAMS} />
          <p>
            <strong className="font-semibold text-slate-900">A group is label agreement, not verified identity.</strong>{" "}
            Nobody has checked that two papers&rsquo; &ldquo;TpPa-1&rdquo; are the same material. Only{" "}
            <Mono>specific</Mono> groups are listed by default and linked on the website; <Mono>generic</Mono> labels
            name a class (&ldquo;COF&rdquo;), <Mono>serial</Mono> ones a per-paper enumeration (&ldquo;COF-1&rdquo;),
            and <Mono>paper_local</Mono> ones a paper&rsquo;s own tag, so matching them across papers would be
            coincidence.
          </p>
        </Section>

        <Section id="papers" title="Searching papers">
          <Table rows={PAPER_PARAMS} />
        </Section>

        <Section id="pagination" title="Pagination">
          <p>
            List endpoints return <Mono>{"{ data, meta }"}</Mono>. <Mono>meta.total</Mono> is the full match count
            and <Mono>meta.next</Mono> is the URL of the next page, or <Mono>null</Mono> on the last one. Follow it
            until it runs out:
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
              <Mono>two_theta_uncertainty_deg</Mono> is the per-curve axis error (median ≈ 0.05°). Peak shapes and
              heights inherit the figure&rsquo;s resolution and any smoothing the authors applied.
            </li>
            <li>
              <Mono>relative_intensity</Mono> is scaled per curve so the tallest feature is roughly 100. It is{" "}
              <strong className="font-semibold text-slate-900">not comparable across curves</strong>.
            </li>
            <li>
              <Mono>first_peak.d_spacing_angstrom_derived</Mono> is computed from 2θ at{" "}
              <Mono>first_peak.wavelength_angstrom</Mono>. Unless <Mono>wavelength_source</Mono> is{" "}
              <Mono>paper_reported</Mono>, that wavelength is an assumed Cu Kα, so the d-spacing carries no information
              the angle does not.
            </li>
            <li>
              <Mono>crystallinity.sharp_feature_fraction</Mono> is the share of intensity in features narrower than
              ~4°. It is not a degree of crystallinity and depends strongly on how much of the pattern was plotted.{" "}
              <Mono>first_peak_to_hump_ratio</Mono> is a height ratio within one curve; no Miller indices were
              assigned.
            </li>
            <li>
              <Mono>unvetted_peaks</Mono> is the raw automatic peak list. It passes none of the checks behind{" "}
              <Mono>first_peak.status</Mono>.
            </li>
            <li>
              <Mono>admission</Mono> says which class a curve is in. Every curve the pipeline accepted is published,
              with its class, rather than the flagged ones being held back:
              <ul className="mt-1.5 list-[circle] space-y-1 pl-5">
                <li>
                  <Mono>clean</Mono> — the curve and every other curve in its figure passed the automated shape
                  checks. The strictest tier, and the one this project&rsquo;s own analyses use.
                </li>
                <li>
                  <Mono>axis_verified</Mono> — this curve passed but another one in the same figure did not, in a
                  figure whose 2θ axis was fit two independent ways that agreed.
                </li>
                <li>
                  <Mono>axis_unconfirmed</Mono> — this curve passed and another one in the figure did not, and the
                  figure&rsquo;s 2θ axis was <em>not</em> confirmed by two independent fits.
                </li>
                <li>
                  <Mono>shape_flagged</Mono> — this curve&rsquo;s own trace failed an automated shape check (a
                  staircase or notch in the line, or a trace that strayed off the printed curve). Compare it with the
                  source figure before relying on it.
                </li>
                <li>
                  <Mono>model_derived</Mono> — a label check after extraction found a simulated, refined or difference
                  curve. Not a measured pattern.
                </li>
              </ul>
            </li>
            <li>
              <strong className="font-semibold text-slate-900">Training a model? Split by paper_id</strong>, never by
              curve. Curves in one figure usually share a material and always share an axis; a random curve-level
              split puts near-duplicates on both sides.
            </li>
          </ul>
        </Section>

        {/* The same component as the About section, so the API's readers — the
            ones most likely to reuse curves in bulk — get the identical citation
            and license terms rather than a paraphrase. */}
        <Section id="citing" title="Citing and license">
          <CitingAndLicense />
        </Section>

        <Section id="conventions" title="Conventions">
          <ul className="list-disc space-y-2.5 pl-5">
            <li>
              <strong className="font-semibold text-slate-900">Stable within v1.</strong> Fields will not be renamed or
              removed under <Mono>/api/v1</Mono>; new fields may be added. A breaking change ships as a new version
              prefix.
            </li>
            <li>
              Errors return <Mono>{"{ error: { code, message } }"}</Mono> with a matching HTTP status. Unknown query
              parameters are rejected with a 400 that lists the valid ones, so a typo never silently returns the
              default page.
            </li>
            <li>Responses are cached for up to an hour. The data changes only when the corpus is re-imported.</li>
            <li>CORS is open, so the API can be called from a browser or a hosted notebook.</li>
            <li>
              Questions and problems:{" "}
              <a href="https://github.com/AaronG211/pxrd_PEAK/issues" target="_blank" rel="noreferrer" className={link}>
                GitHub issues
              </a>
              .
            </li>
          </ul>
        </Section>
      </div>
    </div>
  );
}
