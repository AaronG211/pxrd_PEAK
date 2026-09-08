/**
 * The compact top of the browse page.
 *
 * This replaces a full-viewport marketing hero — badge, 56px headline, three
 * feature tiles, two calls to action — that pushed the first record roughly
 * 1,200 px down the page. On a laptop a researcher saw no data at all on the
 * first screen, and the site's most distinctive capability (searching by peak
 * position) sat two screens below that, behind a disclosure.
 *
 * For a data resource the query interface IS the front door: nobody opens the
 * PDB to read about proteins. So the masthead does the least it can — say what
 * the collection is, state its size, and get out of the way of the search box.
 * The three claims the tiles used to make are true and worth keeping; they moved
 * to the About section, where a first-time reader can find them without every
 * returning visitor paying for them in screen space.
 */
function Metric({ label, value, pending }: { label: string; value: number; pending: boolean }) {
  return (
    <div className="flex flex-col">
      <span
        className="font-mono text-lg font-semibold tabular-nums text-slate-900 sm:text-xl"
        aria-busy={pending || undefined}
      >
        {pending ? "—" : value.toLocaleString()}
      </span>
      <span className="mt-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
        {label}
      </span>
    </div>
  );
}

export function CorpusMasthead({
  papers,
  figures,
  curves,
  loading,
}: {
  papers: number;
  figures: number;
  curves: number;
  loading: boolean;
}) {
  return (
    <section className="border-b border-slate-200 bg-white">
      <div className="section-container flex flex-col gap-5 py-6 lg:flex-row lg:items-end lg:justify-between lg:gap-12 lg:py-7">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-[1.6rem]">
            Powder X-ray diffraction, recovered from the literature
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-600">
            Every curve keeps the published figure crop beside its reconstructed
            trace and a downloadable 2θ / intensity file, so the extraction stays
            inspectable rather than replacing the source.
          </p>
        </div>
        {/* A data resource's size is the only credential that matters on arrival,
            and it is more informative than any of the copy it replaced. */}
        <dl className="flex shrink-0 gap-7 sm:gap-9">
          <Metric label="Papers" value={papers} pending={loading} />
          <Metric label="Figures" value={figures} pending={loading} />
          <Metric label="Curves" value={curves} pending={loading} />
        </dl>
      </div>
    </section>
  );
}
