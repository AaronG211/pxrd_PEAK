/**
 * How to cite the collection, what it is licensed under, and where to send a
 * problem or a removal request.
 *
 * One module so the footer, the About section and the API docs cannot drift
 * apart, which three hand-copied paragraphs would. The license terms follow the
 * repository's LICENSE file; change the two together.
 *
 * WHERE THE CITATION POINTS. The dataset is cited by the repository's GitHub
 * releases page, which holds the bulk files, and the code by the repository
 * itself. Which release matches what the site serves is the API docs' business
 * (RELEASE_TAG in pages/ApiDocsPage), so a new release is named in one place.
 */

export const DATASET_URL = "https://github.com/AaronG211/pxrd_PEAK/releases";

export const CODE_URL = "https://github.com/AaronG211/pxrd_PEAK";

/**
 * The dataset's creator, in full (the citation below abbreviates it).
 * Machine-readable records — the paper pages' JSON-LD — name this as the
 * creator of the digitized data, never a source article's authors: the article
 * is the source, not the digitization.
 */
export const DATASET_CREATOR = "Gao, Shenghan";

/**
 * The dataset reference up to its URL, which pages render as a link after it.
 * Together they read as one reference, ready to paste into a bibliography.
 */
export const DATASET_CITATION_TEXT =
  "Gao, S. (2026). COF-PEAK: powder X-ray diffraction curves digitized from figures in "
  + "the covalent organic framework literature [Data set]. GitHub.";

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
 * The site shows no image from a source article (since 2026-11-11: no figure
 * crop and no trace overlay). The one thing it still reproduces is the caption
 * that a few figures carry, quoted verbatim on the paper page and in the API's
 * `caption` field. 198 of the 2,370 papers carry a Creative Commons license in
 * their Crossref record (outputs/pxrd.db, 2026-10-24), where copyright usually
 * stays with the authors, so the sentence names both owners and makes no claim
 * about which applies.
 */
export const SOURCE_MATERIAL_NOTICE =
  "Figure captions quoted from the source papers remain under their original copyright "
  + "(the publisher's or the authors') and are not covered by either license.";

/**
 * The only contact channel the site publishes. Deliberately no e-mail address:
 * an issue is public, so a problem with a curve is visible to the next reader
 * of that curve, and a removal request has a record.
 */
export const ISSUES_URL = "https://github.com/AaronG211/pxrd_PEAK/issues";
