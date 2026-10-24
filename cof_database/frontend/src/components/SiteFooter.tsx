import {
  ARTICLE_STATUS,
  CODE_DOI_URL,
  CODE_LICENSE_NAME,
  CODE_LICENSE_URL,
  DATASET_CITATION_TEXT,
  DATASET_DOI_URL,
  DATA_LICENSE_NAME,
  DATA_LICENSE_URL,
  ISSUES_URL,
  SOURCE_MATERIAL_NOTICE,
} from "../lib/citation";

const link = "text-blue-700 hover:underline";

/**
 * The footer on every page: how to cite, what is licensed, and where to send a
 * problem or a removal request.
 *
 * On every page because a reader who lands on a paper from a search engine
 * never sees the About section, and a curve page is exactly where someone
 * decides to reuse one. The long form lives in components/CitingAndLicense;
 * both read their wording from lib/citation.
 */
export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-slate-200 bg-white py-10">
      <div className="section-container">
        <div className="flex flex-col gap-1 text-sm sm:flex-row sm:items-baseline sm:justify-between sm:gap-3">
          <p className="font-medium text-slate-700">Open PXRD Database</p>
          <p className="text-slate-500">Traceable diffraction data for materials researchers.</p>
        </div>
        <div className="mt-6 grid gap-6 text-xs leading-relaxed text-slate-500 md:grid-cols-3 md:gap-8">
          <section aria-labelledby="site-footer-cite">
            <h2 id="site-footer-cite" className="font-semibold uppercase tracking-wider text-slate-700">
              How to cite
            </h2>
            <p className="mt-2 text-slate-600 [overflow-wrap:anywhere]">
              {DATASET_CITATION_TEXT}{" "}
              <a href={DATASET_DOI_URL} target="_blank" rel="noreferrer" className={link}>
                {DATASET_DOI_URL}
              </a>
            </p>
            <p className="mt-2">
              Code:{" "}
              <a href={CODE_DOI_URL} target="_blank" rel="noreferrer" className={`${link} [overflow-wrap:anywhere]`}>
                {CODE_DOI_URL}
              </a>{" "}
              ({CODE_LICENSE_NAME}). Article: {ARTICLE_STATUS}. Please also cite the source paper
              of every curve you use; its DOI is shown on each paper page and in the{" "}
              <span className="font-mono">doi</span> field of every API record.
            </p>
          </section>
          <section aria-labelledby="site-footer-license">
            <h2 id="site-footer-license" className="font-semibold uppercase tracking-wider text-slate-700">
              License
            </h2>
            <p className="mt-2">
              Digitized data (curves, points and their metadata):{" "}
              <a href={DATA_LICENSE_URL} target="_blank" rel="noreferrer" className={link}>
                {DATA_LICENSE_NAME}
              </a>
              . Code:{" "}
              <a href={CODE_LICENSE_URL} target="_blank" rel="noreferrer" className={link}>
                {CODE_LICENSE_NAME}
              </a>
              . {SOURCE_MATERIAL_NOTICE}
            </p>
          </section>
          <section aria-labelledby="site-footer-contact">
            <h2 id="site-footer-contact" className="font-semibold uppercase tracking-wider text-slate-700">
              Problems and removal requests
            </h2>
            <p className="mt-2">
              To report a problem with a curve or to request removal of a figure,{" "}
              <a href={ISSUES_URL} target="_blank" rel="noreferrer" className={link}>
                open an issue
              </a>{" "}
              on GitHub.
            </p>
          </section>
        </div>
      </div>
    </footer>
  );
}
