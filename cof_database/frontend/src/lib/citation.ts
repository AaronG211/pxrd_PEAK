/**
 * How to cite the collection, what it is licensed under, and where to send a
 * problem or a removal request.
 *
 * One module so the footer, the About section and the API docs cannot drift
 * apart, which three hand-copied paragraphs would. The license terms follow the
 * repository's LICENSE file; SOURCE_MATERIAL_NOTICE below says more than its
 * closing paragraph does, and LICENSE should be brought into line with it
 * rather than the reverse.
 *
 * WHAT THE DOIs ARE USED FOR. They are citation identifiers. Nothing here may
 * say the files can be downloaded from Zenodo: the record's files are
 * access-restricted at the time of writing (2026-10-24). Where bulk files are
 * offered is the API docs' business (RELEASE_TAG in pages/ApiDocsPage), so a
 * change of host is made in one place and never contradicts this one.
 */

export const DATASET_DOI = "10.5281/zenodo.23539381";
export const DATASET_DOI_URL = `https://doi.org/${DATASET_DOI}`;

export const CODE_DOI = "10.5281/zenodo.23540144";
export const CODE_DOI_URL = `https://doi.org/${CODE_DOI}`;

/**
 * The dataset's creator, in the form the repository's .zenodo.json uses (the
 * citation below abbreviates it). Machine-readable records — the paper pages'
 * JSON-LD — name this as the creator of the digitized data, never a source
 * article's authors: the article is the source, not the digitization.
 */
export const DATASET_CREATOR = "Gao, Shenghan";

/**
 * The dataset reference up to its DOI, which pages render as a link after it.
 * Together they read as one reference, ready to paste into a bibliography.
 */
export const DATASET_CITATION_TEXT =
  "Gao, S. (2026). Open PXRD Database: powder X-ray diffraction curves digitized from figures in "
  + "the covalent organic framework literature (release 2026-10-21) [Data set]. Zenodo.";

/**
 * Stated as exactly this until the article has a venue. Naming a journal before
 * acceptance would put a claim in every reader's reference list that may not
 * survive review.
 */
export const ARTICLE_STATUS = "manuscript in preparation";

export const DATA_LICENSE_NAME = "CC BY 4.0";
export const DATA_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/";
export const CODE_LICENSE_NAME = "MIT";
export const CODE_LICENSE_URL = "https://github.com/AaronG211/pxrd_PEAK/blob/main/LICENSE";

/**
 * What neither license covers, as one sentence for the footer and the long form.
 *
 * Three things on the site are reproduced from the source article rather than
 * produced here: the figure crop, the overlay drawn on it, and — for the few
 * figures that carry one — the caption quoted verbatim on the paper page and in
 * the API's `caption` field. Calling them "the property of their publishers"
 * overstated it: 198 of the 2,370 papers carry a Creative Commons license in
 * their Crossref record (outputs/pxrd.db, 2026-10-24), where copyright usually
 * stays with the authors and the figure is already under the article's own
 * license. What matters to a reuser is the same either way, so the sentence
 * names both owners and makes no claim about which applies.
 */
export const SOURCE_MATERIAL_NOTICE =
  "Source figure crops, trace overlays and quoted figure captions are reproduced only so "
  + "that each trace can be checked against its source; they remain under their original "
  + "copyright (the publisher's or the authors') and are not covered by either license.";

/**
 * The only contact channel the site publishes. Deliberately no e-mail address:
 * an issue is public, so a problem with a curve is visible to the next reader
 * of that curve, and a removal request has a record.
 */
export const ISSUES_URL = "https://github.com/AaronG211/pxrd_PEAK/issues";
