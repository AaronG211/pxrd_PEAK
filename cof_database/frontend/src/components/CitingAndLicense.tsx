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

const link = "font-medium text-blue-700 hover:underline";

/**
 * How to cite, what is licensed, and where to report a problem — the long form,
 * for the About section and the API docs. The site footer carries the same
 * facts in short; both read their strings from lib/citation.
 *
 * The license paragraph separates the two kinds of thing a page shows. The
 * points and their metadata are this project's output and are CC BY. The crop,
 * the overlay and a quoted caption are the source article's, shown so a reader
 * can check the trace against the ink; reusing a curve must not read as
 * permission to reuse the figure or the text it came from.
 */
export function CitingAndLicense() {
  return (
    <div className="space-y-5 text-sm leading-relaxed text-slate-600">
      <div>
        <h3 className="font-semibold text-slate-900">How to cite</h3>
        <p className="mt-1.5">If you use these data, cite the dataset:</p>
        <p className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-slate-800 [overflow-wrap:anywhere]">
          {DATASET_CITATION_TEXT}{" "}
          <a href={DATASET_DOI_URL} target="_blank" rel="noreferrer" className={link}>
            {DATASET_DOI_URL}
          </a>
        </p>
        <p className="mt-2">
          The code that produced it:{" "}
          <a href={CODE_DOI_URL} target="_blank" rel="noreferrer" className={`${link} [overflow-wrap:anywhere]`}>
            {CODE_DOI_URL}
          </a>{" "}
          ({CODE_LICENSE_NAME}). The accompanying article is a {ARTICLE_STATUS}.
        </p>
        <p className="mt-2">
          Cite the source paper of every curve you use as well. Its DOI is shown on each paper
          page and in the <span className="font-mono text-xs">doi</span> field of every API record.
        </p>
      </div>
      <div>
        <h3 className="font-semibold text-slate-900">License</h3>
        <p className="mt-1.5">
          The digitized data — curves, points and their metadata — are released under{" "}
          <a href={DATA_LICENSE_URL} target="_blank" rel="noreferrer" className={link}>
            {DATA_LICENSE_NAME}
          </a>
          . The code is{" "}
          <a href={CODE_LICENSE_URL} target="_blank" rel="noreferrer" className={link}>
            {CODE_LICENSE_NAME}
          </a>
          -licensed. {SOURCE_MATERIAL_NOTICE}
        </p>
      </div>
      <div>
        <h3 className="font-semibold text-slate-900">Problems and removal requests</h3>
        <p className="mt-1.5">
          To report a problem with a curve or to request removal of a figure,{" "}
          <a href={ISSUES_URL} target="_blank" rel="noreferrer" className={link}>
            open an issue
          </a>{" "}
          on GitHub.
        </p>
      </div>
    </div>
  );
}
