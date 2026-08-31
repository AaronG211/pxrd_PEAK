/**
 * Loader for the per-figure `curves.csv.gz` bundles in the `pxrd-assets` bucket.
 *
 * Storage serves these objects with no `Content-Encoding: gzip` header, so the
 * browser hands us the raw deflate stream and we inflate it ourselves through
 * `DecompressionStream`. The demo path (`isSupabaseConfigured === false`) serves
 * a plain `.csv` instead, so compression is decided by the URL, not assumed.
 *
 * Two CSV layouts exist in the wild and both are supported, because columns are
 * resolved by header name rather than by position:
 *   live : series_id,series_label,material_name,sample_state,two_theta_deg,
 *          relative_intensity,two_theta_uncertainty_deg
 *   demo : paper_id,figure_id,series_id,label,material_name,sample_state,
 *          two_theta_deg,relative_intensity
 */

export interface CurvePoint {
  x: number;
  y: number;
}

export interface CurveSeries {
  seriesId: string;
  label: string;
  materialName: string | null;
  sampleState: string | null;
  /** `two_theta_uncertainty_deg` for this series, or null when the column is absent. */
  uncertainty: number | null;
  points: CurvePoint[];
}

export type CurveDataErrorKind =
  /** `DecompressionStream` is missing, so a gzipped bundle cannot be inflated. */
  | "unsupported"
  /** The object could not be fetched, or the server answered with a non-2xx status. */
  | "network"
  /** The file downloaded and parsed, but carried no usable numeric rows. */
  | "empty"
  /** The file downloaded but is not the expected curve CSV. */
  | "format";

export class CurveDataError extends Error {
  readonly kind: CurveDataErrorKind;

  constructor(kind: CurveDataErrorKind, message: string) {
    super(message);
    this.name = "CurveDataError";
    this.kind = kind;
  }
}

/**
 * Human-readable message for anything thrown by {@link fetchFigureCurves},
 * mirroring `describeWorkspaceError` in ./digitization.ts.
 */
export function describeCurveDataError(error: unknown): string {
  if (error instanceof CurveDataError) {
    switch (error.kind) {
      case "unsupported":
        return "This browser cannot unpack the compressed curve file. The plot needs DecompressionStream (Chrome 80+, Safari 16.4+, Firefox 113+). The .csv.gz download above still works everywhere.";
      case "network":
        return `The curve data could not be downloaded. ${error.message}`;
      case "empty":
        return "The curve file downloaded but contained no usable data points, so there is nothing to plot. The source imagery above is unaffected.";
      case "format":
        return `The curve file is not in the expected format. ${error.message}`;
    }
  }
  if (isAbortError(error)) return "Loading the curve data was cancelled.";
  return error instanceof Error ? error.message : String(error);
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : error instanceof Error && error.name === "AbortError";
}

function abortError(): DOMException {
  return new DOMException("The curve data request was aborted.", "AbortError");
}

/* -------------------------------------------------------------------------- */
/* CSV parsing                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * RFC 4180 scanner. Handles quoted fields containing commas, doubled quotes and
 * embedded newlines — all three occur in live data, e.g. the series label
 * `"ZIF-90, ZIF-90 phase"`. Tolerates CRLF, lone CR, a UTF-8 BOM and a trailing
 * newline.
 */
function parseCsv(text: string): string[][] {
  const source = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let dirty = false;

  const endField = () => {
    row.push(field);
    field = "";
    dirty = false;
  };
  const endRow = () => {
    endField();
    // A trailing newline yields one empty trailing field; drop that pseudo-row.
    if (row.length > 1 || row[0] !== "") rows.push(row);
    row = [];
  };

  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    if (quoted) {
      if (char === '"') {
        if (source[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"' && !dirty) {
      quoted = true;
      dirty = true;
      continue;
    }
    if (char === ",") {
      endField();
      continue;
    }
    if (char === "\n") {
      endRow();
      continue;
    }
    if (char === "\r") {
      if (source[i + 1] === "\n") i += 1;
      endRow();
      continue;
    }
    field += char;
    dirty = true;
  }
  if (field !== "" || row.length > 0) endRow();
  return rows;
}

function headerIndex(header: string[], ...names: string[]): number {
  const normalized = header.map((name) => name.trim().toLowerCase());
  for (const name of names) {
    const index = normalized.indexOf(name);
    if (index !== -1) return index;
  }
  return -1;
}

function cell(row: string[], index: number): string {
  return index >= 0 && index < row.length ? row[index].trim() : "";
}

function optionalCell(row: string[], index: number): string | null {
  const value = cell(row, index);
  return value === "" ? null : value;
}

/** `Number("")` is 0, so an empty cell must be rejected before conversion. */
function finiteNumber(row: string[], index: number): number | null {
  const value = cell(row, index);
  if (value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseCurveCsv(text: string): CurveSeries[] {
  const rows = parseCsv(text);
  if (rows.length === 0) {
    throw new CurveDataError("empty", "The curve file is empty.");
  }

  const header = rows[0];
  const seriesIdCol = headerIndex(header, "series_id");
  const xCol = headerIndex(header, "two_theta_deg");
  const yCol = headerIndex(header, "relative_intensity");
  if (seriesIdCol < 0 || xCol < 0 || yCol < 0) {
    throw new CurveDataError(
      "format",
      "Expected series_id, two_theta_deg and relative_intensity columns.",
    );
  }
  const labelCol = headerIndex(header, "series_label", "label");
  const materialCol = headerIndex(header, "material_name");
  const stateCol = headerIndex(header, "sample_state");
  const uncertaintyCol = headerIndex(header, "two_theta_uncertainty_deg");

  const byId = new Map<string, CurveSeries>();
  const order: CurveSeries[] = [];

  for (let i = 1; i < rows.length; i += 1) {
    const row = rows[i];
    const seriesId = cell(row, seriesIdCol);
    if (seriesId === "") continue;
    const x = finiteNumber(row, xCol);
    const y = finiteNumber(row, yCol);
    // One malformed row must never kill the file.
    if (x === null || y === null) continue;

    let series = byId.get(seriesId);
    if (!series) {
      series = {
        seriesId,
        label: optionalCell(row, labelCol) ?? seriesId,
        materialName: optionalCell(row, materialCol),
        sampleState: optionalCell(row, stateCol),
        uncertainty: null,
        points: [],
      };
      byId.set(seriesId, series);
      order.push(series);
    }
    if (series.uncertainty === null && uncertaintyCol >= 0) {
      series.uncertainty = finiteNumber(row, uncertaintyCol);
    }
    series.points.push({ x, y });
  }

  const usable = order.filter((series) => series.points.length > 0);
  if (usable.length === 0) {
    throw new CurveDataError("empty", "No usable data points were found.");
  }
  return usable;
}

/* -------------------------------------------------------------------------- */
/* Fetching                                                                    */
/* -------------------------------------------------------------------------- */

function isGzip(url: string): boolean {
  return /\.gz(?:[?#]|$)/i.test(url);
}

function decompressionSupported(): boolean {
  return typeof DecompressionStream !== "undefined";
}

async function readBody(response: Response, gzipped: boolean): Promise<string> {
  if (!gzipped) return response.text();
  // `response.body` is null in a few environments (and for cached opaque reads),
  // so fall back to a Blob-backed stream rather than failing outright.
  const source = response.body ?? (await response.blob()).stream();
  const inflated = source.pipeThrough(new DecompressionStream("gzip"));
  return new Response(inflated).text();
}

async function loadCurves(url: string, signal: AbortSignal): Promise<CurveSeries[]> {
  const gzipped = isGzip(url);
  if (gzipped && !decompressionSupported()) {
    throw new CurveDataError("unsupported", "DecompressionStream is not available.");
  }

  let response: Response;
  try {
    response = await fetch(url, { signal });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new CurveDataError(
      "network",
      error instanceof Error ? error.message : "The request failed.",
    );
  }
  if (!response.ok) {
    throw new CurveDataError("network", `The server responded with ${response.status}.`);
  }

  let text: string;
  try {
    text = await readBody(response, gzipped);
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new CurveDataError(
      "network",
      gzipped
        ? "The compressed curve file could not be unpacked."
        : "The curve file could not be read.",
    );
  }
  return parseCurveCsv(text);
}

type CacheEntry = {
  promise: Promise<CurveSeries[]>;
  controller: AbortController;
  waiters: number;
  settled: boolean;
  /** Monotonic tick of the last read, for LRU eviction. */
  lastUsed: number;
};

/**
 * Keyed by URL and populated with the IN-FLIGHT promise, so a StrictMode double
 * mount (or several figure cards sharing one bundle) issues a single request.
 * Failures are evicted so a retry can refetch.
 *
 * BOUNDED. A resolved entry holds the whole parsed point set: live bundles reach
 * 18,072 points, about 1.3 MB of heap per figure, and this Map is module state in
 * a single-page app, so router navigation never releases it. Unbounded, a session
 * that browses ~100 figures accumulates roughly 130 MB reclaimable only by a full
 * reload. Sixteen entries is far more than any one paper needs (the largest live
 * paper has well under that many figures) while keeping the worst case near
 * 20 MB.
 */
const MAX_CACHED_FIGURES = 16;
const cache = new Map<string, CacheEntry>();
let cacheClock = 0;

/**
 * Drop least-recently-used settled entries until the cache is back in budget.
 *
 * An entry with waiters, or one still in flight, is never evicted: dropping it
 * would orphan the promise its callers are already attached to and lose the
 * de-duplication this cache exists for.
 */
function evictOverflow(): void {
  while (cache.size > MAX_CACHED_FIGURES) {
    let oldestKey: string | null = null;
    let oldestTick = Infinity;
    for (const [key, entry] of cache) {
      if (!entry.settled || entry.waiters > 0) continue;
      if (entry.lastUsed < oldestTick) {
        oldestTick = entry.lastUsed;
        oldestKey = key;
      }
    }
    if (oldestKey === null) return;
    cache.delete(oldestKey);
  }
}

export function fetchFigureCurves(
  dataUrl: string,
  signal?: AbortSignal,
): Promise<CurveSeries[]> {
  if (signal?.aborted) return Promise.reject(abortError());

  let entry = cache.get(dataUrl);
  if (!entry) {
    const controller = new AbortController();
    const created: CacheEntry = {
      controller,
      waiters: 0,
      settled: false,
      lastUsed: (cacheClock += 1),
      promise: loadCurves(dataUrl, controller.signal),
    };
    // Every caller may abort before the shared request settles; keep its
    // rejection handled so it never surfaces as an unhandled rejection.
    created.promise.catch(() => undefined);
    created.promise.then(
      () => {
        created.settled = true;
      },
      () => {
        created.settled = true;
        if (cache.get(dataUrl) === created) cache.delete(dataUrl);
      },
    );
    cache.set(dataUrl, created);
    evictOverflow();
    entry = created;
  }

  const active = entry;
  active.lastUsed = (cacheClock += 1);
  active.waiters += 1;

  return new Promise<CurveSeries[]>((resolve, reject) => {
    let done = false;
    const release = () => {
      if (done) return;
      done = true;
      active.waiters -= 1;
      signal?.removeEventListener("abort", onAbort);
      // The shared request is only cancelled once nobody is still waiting on it,
      // and then only after the current task has finished.
      //
      // React StrictMode runs mount -> cleanup -> mount synchronously in one
      // commit. Cancelling inline would abort the request during that window and
      // the remount would either start a second one or, worse, attach to the
      // promise we just rejected and surface a spurious "loading was cancelled"
      // error. Deferring by a microtask lets the remount re-register as a waiter
      // first, while a genuine navigate-away still cancels one tick later.
      if (active.waiters <= 0 && !active.settled) {
        queueMicrotask(() => {
          if (active.waiters > 0 || active.settled) return;
          active.controller.abort();
          if (cache.get(dataUrl) === active) cache.delete(dataUrl);
        });
      }
    };
    function onAbort() {
      release();
      reject(abortError());
    }
    signal?.addEventListener("abort", onAbort, { once: true });
    active.promise.then(
      (series) => {
        if (done) return;
        release();
        resolve(series);
      },
      (error: unknown) => {
        if (done) return;
        release();
        reject(error);
      },
    );
  });
}

/** Test/maintenance hook: forget everything already downloaded. */
export function clearCurveDataCache(): void {
  cache.clear();
}
