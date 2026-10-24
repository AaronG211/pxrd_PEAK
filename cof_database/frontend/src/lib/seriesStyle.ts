/**
 * Stroke colour and dash for each trace in the interactive plot.
 *
 * WHY BY POSITION, NOT BY ROLE. Strokes used to be keyed to the curve's role, in
 * two shades per role plus dash patterns. Roles are nearly binary in this corpus
 * (6,028 of the 11,445 published curves are `unclassified`, 5,332
 * `experimental`), and in 2,303 of the 2,576 multi-curve figures every curve has
 * the same one (outputs/pxrd.db, 2026-10-24). So a four-curve figure came out as
 * two shades of one hue told apart only by dashes, while the source figure
 * beside it showed four coloured, offset traces. Every series now gets its own
 * hue, in series order, and the legend, the readout and the plot's own labels
 * take the same value from here.
 *
 * WHY NOT THE COLOUR THE MODEL REPORTED. The extraction records a colour name
 * per curve (`curves.color` in the local database: "red", "black", "light
 * blue", 58 distinct free-text values), but it is not published: there is no
 * such column in pxrd_curves and none in the per-figure CSV, so this bundle
 * cannot see it. It would not be enough on its own either: in 350 of the 2,576
 * multi-curve figures two traces share a colour name, and printed hues such as
 * yellow or light grey would fail the contrast floor below.
 *
 * THE PALETTE is Okabe & Ito's colour-blind-safe set, restricted to the members
 * that clear WCAG 1.4.11's 3:1 against the white plot background, computed from
 * WCAG 2.x relative luminance:
 *
 *   black            #000000   21.00
 *   vermillion       #D55E00    3.87
 *   blue             #0072B2    5.19
 *   bluish green     #009E73    3.42
 *   reddish purple   #CC79A7    3.06
 *
 * Orange (#E69F00, 2.25), sky blue (#56B4E9, 2.31) and yellow (#F0E442, 1.32)
 * are left out. Darkening them until they pass was tried and rejected: orange
 * darkened to 3:1 sits CIEDE2000 5.0 from vermillion under simulated
 * deuteranopia (Machado et al. 2009, severity 1), and sky blue darkened to 3:1
 * sits 7.0 from bluish green under tritanopia, against at least 12.2 between any
 * two of the five kept, under normal vision and all three simulated
 * deficiencies. That is no worse than the full eight-colour Okabe-Ito set,
 * whose own closest pair is 10.9 (tritan) and 11.6 (deutan).
 *
 * The ORDER is not Okabe-Ito's own. It puts the most separable colours first,
 * because most figures are small: of the 4,664 published figures, 2,088 carry
 * one curve and 4,024 carry four or fewer (outputs/pxrd.db, 2026-10-24). Black
 * then vermillion then blue keeps the closest pair in a three-curve figure at
 * CIEDE2000 38.2 or more under every simulation; Okabe-Ito's own order of these
 * five would put bluish green beside blue at 12.4 (tritan). Re-check with a
 * contrast calculator and a CVD simulation before changing any value here; do
 * not re-assert the table.
 */
export const SERIES_PALETTE: readonly string[] = [
  "#000000",
  "#D55E00",
  "#0072B2",
  "#009E73",
  "#CC79A7",
];

/**
 * Dashes appear only once a figure has more traces than the palette has
 * colours (326 of the 4,664 figures). Five colours against six patterns gives
 * 30 distinct (colour, dash) pairs before anything repeats; the largest figure
 * in the published collection carries 23 curves, so no two traces on one plot
 * share both channels.
 */
const DASH_PATTERNS: readonly (string | undefined)[] = [
  undefined,
  "8 4",
  "2 3",
  "12 3 2 3",
  "4 3",
  "12 3 2 3 2 3",
];

export type SeriesStyle = { color: string; dash: string | undefined };

/**
 * Style for each series id, by its position in the figure.
 *
 * Callers pass every series, hidden ones included, so hiding a trace from the
 * legend never recolours the others.
 */
export function styleSeries(seriesIds: readonly string[]): Map<string, SeriesStyle> {
  const styles = new Map<string, SeriesStyle>();
  seriesIds.forEach((seriesId, index) => {
    styles.set(seriesId, {
      color: SERIES_PALETTE[index % SERIES_PALETTE.length],
      dash: DASH_PATTERNS[Math.floor(index / SERIES_PALETTE.length) % DASH_PATTERNS.length],
    });
  });
  return styles;
}
