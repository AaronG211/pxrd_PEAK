/**
 * Cross-paper material LABEL grouping.
 *
 * WHAT THIS IS. Two curves land in the same group when the material names their
 * papers printed normalise to the same string. That is a claim about LABEL
 * AGREEMENT and nothing more. Nobody has verified that the two samples are the
 * same material, and every surface built on this module is worded accordingly.
 *
 * WHY NOT `peter_match`. The recon that preceded this work measured the
 * alternative: `peter_match.peter_row` is DOI-gated at build time, so all 475
 * tier-A groups in the 1,000-paper pilot span exactly one paper — the column is
 * structurally incapable of expressing cross-paper identity. Its only
 * cross-paper field, `matched_name`, is a normalised copy of a third-party
 * dataset's content column and may not be republished. It finds 18 groups over
 * 40 papers; the in-house `material_name` finds 100 raw / 76 defensible / 51
 * strict groups over 124-167 papers, 17 of the 18 included. So this module reads
 * `pxrd_curves.material_name` and nothing else.
 *
 * THE SPECIFICITY GATE IS THE POINT. The biggest raw groups are the worst ones:
 * `COF` is the literal word (7 unrelated papers), and `COF-1`...`COF-5` are
 * per-paper serial numbering — one pilot paper alone supplies COF-1 through
 * COF-5. Linking those would assert that one paper's "COF-2" IS another's, which
 * is a fabricated identity claim. Only `specific` groups are ever surfaced;
 * `generic`, `serial` and `paper_local` labels get no badge, no link and no
 * filter match. That deliberately throws away the largest and most
 * impressive-looking groups.
 *
 * The normaliser is the repaired one from tools/repair_peter_match.py, with the
 * defect that recon found fixed: `@` and `/` are IDENTITY-BEARING and are kept,
 * so the composite `COF@S` no longer collides with the plural `COFs`, and
 * `COF/MXene` no longer merges with `COF@MXene`.
 */

export type LabelSpecificity = "specific" | "generic" | "serial" | "paper_local";

/** How the raw labels in a group agreed. Neither value claims material identity. */
export type LabelMatchBasis = "label_identical" | "label_normalised";

export interface MaterialLabelGroup {
  /** The normalised label. Stable, and safe in a URL path segment. */
  key: string;
  /** The most frequent raw label, shown to the reader instead of the key. */
  displayName: string;
  /** Every raw label folded into this group, so a mismatch is visible. */
  variants: string[];
  paperIds: string[];
  paperCount: number;
  curveCount: number;
  specificity: LabelSpecificity;
  matchBasis: LabelMatchBasis;
}

export interface MaterialLabelIndex {
  /** Every group, including the ones the gate refuses to surface. */
  byKey: Map<string, MaterialLabelGroup>;
  /** paperId -> surfaceable group keys (specific, and spanning >1 paper). */
  sharedByPaper: Map<string, string[]>;
  /** Counts over the surfaceable groups only, for honest UI copy. */
  sharedGroupCount: number;
  sharedPaperCount: number;
  /** Every distinct normalised label seen, surfaceable or not. */
  totalLabelCount: number;
}

export interface MaterialLabelEntry {
  materialName: string | null;
  paperId: string;
}

/*
 * EVERYTHING BELOW MIRRORS cof_database/scripts/material_labels.py.
 *
 * That module is the importer's copy and will eventually populate
 * `pxrd_material_groups` / `pxrd_curves.material_group_id`. This one computes
 * the same answer in the browser so the feature works before that migration
 * runs. The two MUST agree: a page that links a pair of papers today and stops
 * linking them after a deploy — or the reverse — is exactly the quiet
 * inconsistency this project exists to avoid. Change them together.
 */

/**
 * Unicode sub/superscript forms carry composition: Fe3O4 and Fe₃O₄ are one
 * material written two ways. The letter forms matter too — live labels include
 * `COFₚₙ` and `COFₐ-2`, and dropping the subscript letter folds both into the
 * bare generic word `cof`.
 */
const CHARACTER_FORMS: Record<string, string> = {
  "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
  "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
  "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
  "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
  "ₐ": "a", "ₑ": "e", "ₕ": "h", "ᵢ": "i", "ₙ": "n", "ₒ": "o",
  "ₚ": "p", "ₛ": "s", "ₜ": "t", "ⁿ": "n", "ᵃ": "a", "ᵇ": "b",
};

/**
 * Spelled out rather than folded to a latin letter: `alpha-COF` and `a-COF` are
 * not the same string in any paper, and collapsing gamma to "g" would invent a
 * collision with a real "g-" prefix.
 */
const GREEK: Record<string, string> = {
  α: "alpha", β: "beta", γ: "gamma", δ: "delta", ε: "epsilon",
  ζ: "zeta", η: "eta", θ: "theta", κ: "kappa", λ: "lambda",
  μ: "mu", ν: "nu", π: "pi", ρ: "rho", σ: "sigma", τ: "tau",
  φ: "phi", χ: "chi", ψ: "psi", ω: "omega",
};

const MARKUP = /<\/?[a-z][^>]{0,40}>/gi;
const ENTITIES: Record<string, string> = {
  "&amp;": "&", "&lt;": "<", "&gt;": ">", "&nbsp;": " ", "&quot;": '"',
};

/** Bare framework-class words. On their own they name a field, not a material. */
const FRAMEWORK_STEM =
  "(?:cofs?|mofs?|zifs?|pofs?|cmps?|ctfs?|pafs?|hofs?|cops?|imps?|pims?|sbas?|zsms?)";
/** Stems that are counters rather than names, so `<stem><n>` is enumeration. */
const COUNTER_STEM =
  "(?:sample|compound|complex|material|polymer|catalyst|product|entry|no)";

/**
 * A whole label that is only a class word, a process adjective, or a linkage
 * chemistry. "Imine-COF" and "Amide COF" name a bond, not a compound: two
 * papers' imine COFs are routinely different materials.
 */
const GENERIC_EXACT = new Set([
  "cof", "cofs", "mof", "mofs", "zif", "zifs", "pof", "pofs", "cmp", "cmps",
  "ctf", "ctfs", "paf", "pafs", "hof", "hofs", "cop", "cops",
  "framework", "frameworks", "polymer", "polymers", "resin",
  "sample", "samples", "specimen", "blank", "control", "reference",
  "standard", "pristine", "product", "material", "materials", "compound",
  "compounds", "complex", "powder", "solid", "crystal", "crystals",
  "bulk", "film", "films", "membrane", "membranes", "composite",
  "composites", "catalyst", "catalysts", "support", "precursor", "monomer",
  "ligand", "adsorbent", "asprepared", "assynthesized", "assynthesised",
  "asmade", "titlecomplex", "titlecompound", "thiswork", "ourcof",
  "experimental", "simulated", "calculated", "observed",
]);

/**
 * Linkage and process adjectives that qualify a class word without naming a
 * compound. `<adjective><framework>` is refused for the same reason as above.
 */
const CLASS_ADJECTIVE =
  "(?:imine|imino|amide|amido|amine|amino|azine|azo|hydrazone|boronate|"
  + "boroxine|borate|ketoenamine|enamine|olefin|vinylene|triazine|"
  + "covalent|crystalline|amorphous|porous|pure|raw|neat|blank|parent|"
  + "pristine|bare|fresh|used|spent|recovered|recycled|regenerated|"
  + "activated|calcined|hydrated|dried|wet|dry|thin|thick|nano|micro|meso|"
  + "macro|hollow|solid|magnetic|modified|functionalized|functionalised|"
  + "doped|loaded|supported|ref|reference|control|standard|new|old|"
  + "synthesized|synthesised|prepared|obtained|target|initial|final)";
const PROCESS_SUFFIX =
  "(?:film|films|membrane|membranes|powder|composite|bulk|sample|nanosheets?|nanoparticles?|ns|np|nps)";

/** 1. A class word carrying only an adjective or a process suffix. */
const GENERIC_PATTERN = new RegExp(
  `^(?:${CLASS_ADJECTIVE})?${FRAMEWORK_STEM}(?:${PROCESS_SUFFIX})?$`,
);
/**
 * 2. Enumeration: a class or counter stem plus a short local tag. The tag may be
 * a number (COF-1, compound 2) or a bare letter token (COF-A, COF-H, COF-Br,
 * COF-OMe) — papers assign both the same way, so both are refused. Two
 * exemptions survive, because both are the literature's catalogue form rather
 * than a local tag: three or more digits (COF-300, COF-366), and two or three
 * letters followed by a number (COF-LZU1, COF-NEU1). A single letter before the
 * number is NOT exempt: COFa-2 is a variant tag.
 */
const SERIAL_PATTERN = new RegExp(
  `^(?:${FRAMEWORK_STEM}|${COUNTER_STEM})(?:\\d{1,2}[a-z]?|[a-z]\\d{1,2}|[a-z]{1,3})$`,
);
/**
 * 3. A short prefix bolted onto a class word, with an optional small serial:
 * M-COF, H-COF, P-COF, Bpy-COF, TT-COF, eCOF-2, H-COF1. These are how a paper
 * names its own variants. The rule also refuses some genuine institutional
 * catalogue names (NKCOF-1, TTI-COF); no string test separates the two cases,
 * and refusing is the safe direction.
 */
const PAPER_LOCAL_PATTERN = new RegExp(
  `^[a-z]{1,3}\\d{0,2}${FRAMEWORK_STEM}(?:[a-z]?\\d{1,2})?$`,
);

/** A key too short to identify anything: "1", "4", "1a", "AB", "MS", "CS". */
const MIN_KEY_LENGTH = 3;

/**
 * Fold a printed material label to its comparison key.
 *
 * Formatting is discarded; composition and composite structure are kept.
 * Everything outside `[a-z0-9@/]` is dropped. `@` and `/` survive because they
 * are the composite operators — `COF@S` is a sulfur composite and `COFs` is a
 * plural, so deleting `@` would merge them, as it would `COF/MXene` with
 * `COF@MXene`. Returns "" for anything with no alphanumeric content, which
 * never groups.
 */
export function normalizeMaterialLabel(raw: string | null | undefined): string {
  if (!raw) return "";
  let text = raw;
  for (const [entity, char] of Object.entries(ENTITIES)) {
    text = text.split(entity).join(char);
  }
  // The tag goes, its text stays: a normaliser that deletes everything outside
  // [a-z0-9] turns "TpPa-NH<sub>2</sub>" into "tppanhsub2sub", which can never
  // equal "tppanh2".
  text = text.replace(MARKUP, "").toLowerCase();
  let folded = "";
  for (const char of text) {
    folded += CHARACTER_FORMS[char] ?? GREEK[char] ?? char;
  }
  return folded.replace(/[^a-z0-9@/]/g, "");
}

/**
 * Classify a normalised key. Only `specific` may ever link papers.
 *
 *   generic     names a class or a process, not a compound  ("COF", "Imine-COF")
 *   serial      per-paper enumeration                       ("COF-1", "COF-A")
 *   paper_local a paper's own variant tag                   ("M-COF", "H-COF1")
 *   specific    everything else                             ("TpPa-1", "COF-300")
 */
export function classifyLabelSpecificity(key: string): LabelSpecificity {
  if (!key || key.length < MIN_KEY_LENGTH) return "paper_local";
  if (GENERIC_EXACT.has(key) || GENERIC_PATTERN.test(key)) return "generic";
  if (SERIAL_PATTERN.test(key)) return "serial";
  if (PAPER_LOCAL_PATTERN.test(key)) return "paper_local";
  return "specific";
}


/** A group may be surfaced only when it is specific AND actually spans papers. */
export function isSurfaceableGroup(group: MaterialLabelGroup): boolean {
  return group.specificity === "specific" && group.paperCount > 1;
}

/**
 * Most frequent raw label wins; ties break on the shortest, then alphabetically,
 * so the display name is stable across loads and matches the importer's.
 */
function pickDisplayName(counts: Map<string, number>): string {
  let best = "";
  let bestCount = -1;
  for (const [name, count] of counts) {
    if (
      best === ""
      || count > bestCount
      || (count === bestCount
        && (name.length < best.length
          || (name.length === best.length && name < best)))
    ) {
      best = name;
      bestCount = count;
    }
  }
  return best;
}

/**
 * Build the label index from `(material_name, paper_id)` pairs.
 *
 * Curves with a blank material name contribute nothing: the absence of a label
 * is not a label, and must never become a group of its own.
 */
export function buildMaterialLabelIndex(entries: MaterialLabelEntry[]): MaterialLabelIndex {
  type Accumulator = {
    variants: Map<string, number>;
    paperIds: Set<string>;
    curveCount: number;
  };
  const accumulators = new Map<string, Accumulator>();

  for (const entry of entries) {
    const raw = entry.materialName?.replace(/\s+/g, " ").trim();
    if (!raw) continue;
    const key = normalizeMaterialLabel(raw);
    if (!key) continue;
    let accumulator = accumulators.get(key);
    if (!accumulator) {
      accumulator = { variants: new Map(), paperIds: new Set(), curveCount: 0 };
      accumulators.set(key, accumulator);
    }
    accumulator.variants.set(raw, (accumulator.variants.get(raw) ?? 0) + 1);
    accumulator.paperIds.add(entry.paperId);
    accumulator.curveCount += 1;
  }

  const byKey = new Map<string, MaterialLabelGroup>();
  const sharedByPaper = new Map<string, string[]>();
  const sharedPapers = new Set<string>();
  let sharedGroupCount = 0;

  for (const [key, accumulator] of accumulators) {
    const variants = [...accumulator.variants.keys()].sort();
    const group: MaterialLabelGroup = {
      key,
      displayName: pickDisplayName(accumulator.variants),
      variants,
      paperIds: [...accumulator.paperIds].sort(),
      paperCount: accumulator.paperIds.size,
      curveCount: accumulator.curveCount,
      specificity: classifyLabelSpecificity(key),
      matchBasis: variants.length === 1 ? "label_identical" : "label_normalised",
    };
    byKey.set(key, group);
    if (!isSurfaceableGroup(group)) continue;
    sharedGroupCount += 1;
    for (const paperId of group.paperIds) {
      sharedPapers.add(paperId);
      const existing = sharedByPaper.get(paperId);
      if (existing) existing.push(key);
      else sharedByPaper.set(paperId, [key]);
    }
  }

  for (const keys of sharedByPaper.values()) keys.sort();

  return {
    byKey,
    sharedByPaper,
    sharedGroupCount,
    sharedPaperCount: sharedPapers.size,
    totalLabelCount: accumulators.size,
  };
}

/** The surfaceable groups a single paper participates in, largest first. */
export function sharedGroupsForPaper(
  index: MaterialLabelIndex,
  paperId: string,
): MaterialLabelGroup[] {
  const keys = index.sharedByPaper.get(paperId) ?? [];
  return keys
    .flatMap((key) => {
      const group = index.byKey.get(key);
      return group ? [group] : [];
    })
    .sort((a, b) => b.paperCount - a.paperCount || a.displayName.localeCompare(b.displayName));
}

/**
 * The group a single printed label belongs to, or null when that label is not
 * surfaceable. Null covers three different facts — the label is unique to this
 * paper, the label is generic, or the label is a paper-local serial — so the
 * caller must not render null as "unique material".
 */
export function groupForLabel(
  index: MaterialLabelIndex,
  materialName: string | null,
): MaterialLabelGroup | null {
  const key = normalizeMaterialLabel(materialName);
  if (!key) return null;
  const group = index.byKey.get(key);
  return group && isSurfaceableGroup(group) ? group : null;
}

export const EMPTY_MATERIAL_LABEL_INDEX: MaterialLabelIndex = {
  byKey: new Map(),
  sharedByPaper: new Map(),
  sharedGroupCount: 0,
  sharedPaperCount: 0,
  totalLabelCount: 0,
};
