"""Trim page text that the figure crop caught along with the panel.

crop.png is a rectangle cut from the rendered PDF page around one plot, and the
rectangle is generous: it routinely takes in the first line or two of the
article's figure caption below the panel, and sometimes the journal's running
header ("pubs.acs.org/cm", "Journal of Solid State Chemistry 277 ...") with its
full-width rule above it. On the site those strips read as part of the figure.
This module finds them and returns a crop box without them; the importer applies
it to source.webp and overlay.webp. Nothing on disk is changed.

What the module knows, and how it knows it
------------------------------------------
The plot frame is not guessed. render_overlay (src/pxrd_fetcher/v2/vision.py)
drew it into overlay.png as a 1-px RGB (0, 200, 0) rectangle on the same pixel
grid as crop.png, and frame_from_overlay reads it back exactly. A figure whose
rectangle does not read cleanly - not exactly two long rows and two long
columns of that colour - is not trimmed at all.

Everything else is page geometry measured from the crop's own ink:

  Below the frame. The panel's x-axis comes first: the tick labels, then the
  axis title, both kept whatever they look like (when the first band is only
  the rest of the box line and the tick marks, the two bands after it). A
  caption line is a run of type that starts at the column margin, overhangs
  the frame on at least one side, covers most of the frame's width, and is
  inked evenly across that run. Being type, it holds no drawn line (no
  horizontal run of ink longer than any glyph stroke: an x-axis, a box edge)
  and is not solid (few of its columns are inked over the line's full height,
  unlike a dashed outline or the top of a photograph). Being set to the
  column, it runs off the crop or reaches, on at least one side, as far out
  as the panel's own outermost ink (y-axis title, tick labels, a neighbour
  set close by); a line centred inside that extent, such as a subtitle under
  the axis title, is the panel's. The cut goes in the blank gap above the
  first band after the axis that looks like that, and only when every band
  below it is page text as well (more lines of type, a rule, a small mark, or
  what the crop's bottom edge slices): a plot, a photograph or an outline
  under the line means the line is inside the figure, and the scan moves on.
  When no band qualifies the bottom is left alone. Some crops slice a caption
  line through, leaving only glyph tops on the last pixel row; those are
  recognised by ink in nearly every narrow column bin across the frame, which
  a row of tick labels never achieves. A legend row or a note set below the
  axis title in caption-like type that also reaches out to the panel's edge
  cannot be told from a caption this way and is cut with it.

  Above the frame. A running header is type touching a crop edge at the very
  top and running off it; a page rule is a 1-3 px line, at least 90% inked,
  that touches a crop edge and runs past the frame on both sides; a line the
  crop's top edge slices through is page text too. Lines of body text above
  the panel are wide, evenly inked type, but so is a panel title or a legend
  row, so a wide line is cut only when it follows, at ordinary leading, page
  text of one of the kinds above or another line of its paragraph. A band
  that falls apart into two lines when read over the frame's own columns is
  page text joined to the panel's legend or title (through a panel letter
  beside them) and is never taken for page text. The scan stops at the first
  band that is none of these (a panel letter, a legend, a title) and keeps it.

What it refuses to do
---------------------
It never cuts inside the frame, never cuts the left or right edge, and never
cuts through ink: every cut is on a row of a blank gap between two bands at
which nothing is inked on both sides of the boundary, across the whole crop
width and not only where the bands were read, so a neighbouring panel's
letters or a second text column are not sliced. The one exception is a thin
vertical line (a neighbour's frame, a column rule) crossing the gap from well
above to well below. A gap with no such row is not cut. A band below the frame
that holds a drawn line is never taken for a caption and never removed, so a
stack of panels is not cut between its members, even when the stack is
staggered and the next plot's axis starts outside this frame. A frame
narrower or shorter than MIN_FRAME_FRAC of the crop is a frame the pipeline
drew around a sub-plot or an inset, and that figure is not trimmed either.

The thresholds below are fractions of the crop width, so they hold at any
render DPI, and every one of them was set against contact sheets of crops with
the cut lines drawn, not chosen in advance. The first set came from a local
pool of 279 figures, 48 of them real crop/overlay pairs and the rest rebuilt
from QA montages with an estimated frame, plus synthetic panels. The rules
for drawn lines, solid marks, the panel's reach, page text under the caption,
stacked bands above the frame and the ink-free boundary were added after the
first run over the stored corpus (4,664 figures), against the harmful cuts
reviewers found there and contact sheets of every cut those rules changed.
The importer still applies the trim only on request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# render_overlay's frame colour. Exact match only: anti-aliased trace pixels
# never land on it, which is what makes the rectangle machine-readable.
FRAME_RGB = (0, 200, 0)
# A pixel is ink when its lightest channel is below this. Catches black and
# coloured type (a red caption label has a dark G and B) and leaves the pale
# fills some journals put behind panels.
INK_MAX_CHANNEL = 185
# A column inked in this fraction of a region's rows is a vertical spine (a
# neighbouring panel's frame, a column rule), not text; it is ignored there.
PERSISTENT_COLUMN_FRAC = 0.8

# A caption or body-text line spans at least this much of the crop...
WIDE_FRAC = 0.55
# ...reaches at least this far beyond the frame on one side...
OVERHANG_FRAC = 0.04
# ...and covers at least this much of the frame's own width.
FRAME_COVER_FRAC = 0.6
# Equal slices a run is split into to ask whether ink is spread along all of it
# (type) or bunched at one end (a legend, a label pair).
SPREAD_BINS = 20
# A band's head (top rows, below the frame) or tail (bottom rows, above it)
# must hold ink in this fraction of SPREAD_BINS: the caption's first line is
# fully set, while a label row merged with a long line below it is not.
HEAD_SPREAD = 0.5
# Rows in that head or tail.
SLICE_FRAC = 0.015
# Or: one row in the band's top half inked in this fraction of SPREAD_BINS.
FULL_ROW_SPREAD = 0.8
# No stroke of type is longer than this along a row.
GLYPH_STROKE_FRAC = 0.03

# Blank rows needed between two bands for a cut to go between them.
MIN_GAP_FRAC = 0.004
# Taller bands are not one line of type (a heading at most ~2 lines at 1501 px).
TEXT_MAX_FRAC = 0.08
# A first band below the frame no taller than this is read as the rest of the
# box line and the tick marks (outward ticks measured 0.9-2.4% of the width on
# synthetic panels rendered like the pipeline's). The tick labels are then the
# second band and the x-axis title the third, so neither can be the first
# caption line. Tick labels this small are read the same way, which costs at
# most one caption line kept.
TICK_BAND_FRAC = 0.025
# Ordinary leading: the blank rows between two lines of one paragraph are at
# most this fraction of a line's height (0.06-0.33 between the lines of page
# text cut in the local pool). Panel content above the frame (a title, a
# legend row) is normally set further apart from the page text above it.
LEADING_FRAC = 0.6
# Horizontal window that bands below the frame are read in: from a little left
# of the frame to a little right of it. Ink further out is a neighbouring panel,
# and reading it would merge that panel's tick labels with this one's.
WINDOW_LEFT_FRAC = 0.06
WINDOW_RIGHT_FRAC = 0.03

# Cut-through text line: ink in this fraction of SPREAD_BINS over the whole
# band, and of FINE_BINS over the frame's own span.
EDGE_BIN_COVER = 0.75
FINE_BINS = 40
FINE_BIN_COVER = 0.8

# A running header: type touching a crop edge, at least this wide, starting
# within HEADER_ZONE_FRAC of the top.
HEADER_FRAC = 0.15
HEADER_ZONE_FRAC = 0.03
# ...and running off the crop: ink within this of the first or last column
# (at least 2 px). The header is set across the page, wider than any crop, so
# the crop slices it; a panel heading that merely sits near the crop's corner
# stops short of the edge (8 px in, on the synthetic case that was cut). On
# the local pool every running header reached within 2 px of an edge.
HEADER_EDGE_FRAC = 0.002
# Minimum length of the short last line of a paragraph.
CONTINUATION_FRAC = 0.15

# A page rule is at most this thick, and runs this far past the frame on each
# side (or off a crop edge the frame itself reaches).
RULE_MAX_FRAC = 0.008
RULE_PAST_FRAC = 0.02
# A rule is unambiguous, so the blank gap above it may be this narrow (px).
RULE_MIN_GAP = 2
# How close to a crop edge counts as touching it.
EDGE_TOUCH_FRAC = 0.005

# A frame smaller than this, in either dimension, is around a sub-plot or an
# inset; the page geometry above does not describe it.
MIN_FRAME_FRAC = 0.3

# A horizontal run of ink this long, anywhere in a band, is a drawn line (an
# x-axis, a plot box edge, a rule), never a stroke of type. A band below the
# frame holding one is figure graphics, wherever the line starts and ends.
DRAWN_LINE_FRAC = 0.1
# A cut may cross a column the region reads as a spine only where that column
# is a thin vertical line: no wider than this, and inked without a break for
# SPINE_REACH_FRAC above and below the cut (or to the crop edge). Glyph stems,
# descenders and photo strips are not.
SPINE_MAX_WIDTH_FRAC = 0.004
SPINE_REACH_FRAC = 0.02
# A caption starts at the column margin, so on at least one side it reaches
# out as far as the panel's own outermost ink (within PANEL_REACH_FRAC), or
# runs off the crop. The line is read with word gaps up to LINE_GAP_FRAC
# closed, so the glyph tops of a line the crop slices still read as one run;
# the panel is the ink beside the frame, column after column, with no blank
# stretch wider than PANEL_GAP_FRAC (a text column across the gutter, or a
# neighbouring panel set further off, is not part of it).
PANEL_REACH_FRAC = 0.01
LINE_GAP_FRAC = 0.05
PANEL_GAP_FRAC = 0.02
# A band is a drawn mark, not type, when this share of its line's width
# (line_run) is columns inked over at least 90% of the band's rows: a dashed
# or solid line, the edge of a photograph, a filled shape. Type is made of
# strokes, so few of its columns run the full height of the line, even when
# the band holds only a line's descenders or the glyph tops a crop leaves.
SOLID_COLUMN_FRAC = 0.35
# Below the caption the crop holds only page text. A band there may also be a
# lone small mark (a page number, a speck) no longer than SMALL_MARK_FRAC, a
# sliver the crop's bottom edge leaves no taller than EDGE_SLIVER_FRAC, or a
# lone thin rule under the caption.
SMALL_MARK_FRAC = 0.05
EDGE_SLIVER_FRAC = 0.004


@dataclass(frozen=True)
class Run:
    left: int
    right: int  # inclusive

    @property
    def length(self) -> int:
        return self.right - self.left + 1


@dataclass(frozen=True)
class Band:
    """Consecutive non-blank rows."""

    top: int
    bottom: int  # inclusive

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1


@dataclass(frozen=True)
class Trim:
    frame: tuple[int, int, int, int] | None
    box: tuple[int, int, int, int]  # PIL crop box: left, top, right, bottom
    top_cut: bool
    bottom_cut: bool
    reason: str  # "ok", or why the figure was left whole
    # Why the cuts were made, for the corpus check's contact sheets; the
    # importer reads only `box`. bottom_band is the index, counted from the
    # frame down, of the first band removed below it. top_evidence names, for
    # each band removed above the frame, the rule that took it as page text.
    bottom_band: int | None = None
    top_evidence: tuple[str, ...] = ()


def frame_from_overlay(overlay: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) of the rectangle render_overlay drew, or None when it
    does not read as exactly one rectangle. Same reader as
    tools/figures_v4/fig3_end_to_end.py, returning None instead of raising."""
    g = np.all(overlay[..., :3] == np.array(FRAME_RGB), axis=2)
    rows = np.flatnonzero(g.sum(1) > 0.15 * overlay.shape[1])
    cols = np.flatnonzero(g.sum(0) > 0.15 * overlay.shape[0])
    if len(rows) != 2 or len(cols) != 2:
        return None
    return int(cols[0]), int(rows[0]), int(cols[1]), int(rows[1])


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) of each run of True."""
    if not flags.any():
        return []
    padded = np.concatenate(([False], flags, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2])]


def _longest_closed_run(occ: np.ndarray, max_gap: int) -> Run | None:
    """Longest stretch of inked columns once gaps up to `max_gap` are closed:
    the word spaces of a line of type, not the space between two labels."""
    runs = _runs(occ)
    if not runs:
        return None
    merged = [list(runs[0])]
    for a, b in runs[1:]:
        if a - merged[-1][1] - 1 <= max_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    a, b = max(merged, key=lambda r: r[1] - r[0])
    return Run(a, b)


class Page:
    """Ink of one crop, with the frame it was digitized against."""

    def __init__(self, crop: np.ndarray, frame: tuple[int, int, int, int]) -> None:
        self.mask = crop[..., :3].min(axis=2) < INK_MAX_CHANNEL
        self.height, self.width = self.mask.shape
        self.frame = frame
        self.keep = np.ones(self.width, bool)
        self.min_gap = max(3, round(MIN_GAP_FRAC * self.width))
        self.slice = max(4, round(SLICE_FRAC * self.width))
        self.text_max = round(TEXT_MAX_FRAC * self.width)
        # A row with no more inked pixels than this is blank (JPEG speckle).
        self.noise = max(1, self.width // 1000)

    def bands(self, row0: int, row1: int, col0: int = 0, col1: int | None = None) -> list[Band]:
        """Bands of ink in rows [row0, row1), read in columns [col0, col1).

        Also sets which columns the band tests that follow may look at: the
        persistent spines of this region are dropped everywhere.
        """
        sub = self.mask[row0:row1]
        if sub.shape[0] == 0:
            return []
        if sub.shape[0] >= 20:
            self.keep = sub.mean(0) < PERSISTENT_COLUMN_FRAC
        else:
            self.keep = np.ones(self.width, bool)
        window = np.zeros(self.width, bool)
        window[max(0, col0):col1] = True
        nonblank = (sub & (self.keep & window)[None, :]).sum(1) > self.noise
        return [Band(row0 + a, row0 + b) for a, b in _runs(nonblank)]

    def occupancy(self, top: int, bottom: int) -> np.ndarray:
        return self.mask[top:bottom + 1].any(0) & self.keep

    def cut_row(self, upper: Band, lower_top: int, min_gap: int | None = None) -> int | None:
        """Where to cut between band `upper` and the band starting at row
        `lower_top`: the row of the gap nearest its middle at which the cut
        crosses no ink anywhere across the crop width, or None when the gap
        is narrower than min_gap or no such row exists.

        Bands below the frame are read only in a window around it, so a gap
        that is blank there can still hold a neighbouring panel's letters or
        a second text column; a cut through them would leave half-glyph
        slivers along the edge of the published image. A spine dropped from
        `keep` may be crossed, but only where it really is a thin vertical
        line (see clean_boundary)."""
        lo, hi = upper.bottom + 1, lower_top - 1
        if hi - lo + 1 < (self.min_gap if min_gap is None else min_gap):
            return None
        middle = lo + (hi - lo + 1) // 2
        for y in sorted(range(lo, hi + 1), key=lambda r: (abs(r - middle), r)):
            if self.clean_boundary(y):
                return y
        return None

    def clean_boundary(self, y: int) -> bool:
        """Whether cutting between rows y-1 and y (the boundary of a crop box
        whose edge is y) leaves every glyph whole: row y is blank outside the
        spines (up to `noise` specks), and nothing is inked on both sides of
        the boundary except a spine that is a thin vertical line there. A
        region of only a few rows reads glyph stems as spines (a running
        header's descenders, merged with the rule under it, fill 80% of a
        20-row region), which is why a spine is checked again here."""
        row = self.mask[y]
        if (row & self.keep).sum() > self.noise:
            return False
        if y == 0:
            return True
        crossing = row & self.mask[y - 1]
        if not crossing.any():
            return True
        if (crossing & self.keep).any():
            return False
        widest = max(2, round(SPINE_MAX_WIDTH_FRAC * self.width))
        reach = max(3, round(SPINE_REACH_FRAC * self.width))
        a, b = max(0, y - reach), min(self.height, y + reach)
        for left, right in _runs(crossing):
            if right - left + 1 > widest:
                return False
            if not self.mask[a:b, left:right + 1].all(axis=0).any():
                return False
        return True

    def run(self, top: int, bottom: int) -> Run | None:
        # Word spaces grow with type size, so the gap that still counts as
        # "inside the line" scales with the band's height.
        h = bottom - top + 1
        gap = int(np.clip(0.8 * min(h, self.text_max), 0.008 * self.width, 0.05 * self.width))
        return _longest_closed_run(self.occupancy(top, bottom), gap)

    def overhangs(self, left: int, right: int, or_cut: bool = False) -> bool:
        """Ink reaching OVERHANG_FRAC beyond the frame on either side. With
        `or_cut`, ink running off a crop edge that the frame itself reaches
        also counts: overhang is impossible on that side, and type the crop
        slices through continues past it."""
        x0, _, x1, _ = self.frame
        margin = OVERHANG_FRAC * self.width
        if max(x0 - left, right - x1) >= margin:
            return True
        if not or_cut:
            return False
        edge = EDGE_TOUCH_FRAC * self.width
        return (left <= edge and x0 < margin) or (
            right >= self.width - 1 - edge and x1 > self.width - 1 - margin
        )

    def covers_frame(self, left: int, right: int) -> bool:
        x0, _, x1, _ = self.frame
        return min(right, x1) - max(left, x0) >= FRAME_COVER_FRAC * (x1 - x0)

    def is_wide_line(self, band: Band) -> bool:
        r = self.run(band.top, band.bottom)
        if r is None or r.length < WIDE_FRAC * self.width:
            return False
        return self.overhangs(r.left, r.right, or_cut=True) and self.covers_frame(r.left, r.right)

    def spread(self, top: int, bottom: int, run: Run) -> float:
        """Fraction of SPREAD_BINS equal slices of `run` inked in rows
        [top, bottom]."""
        occ = self.occupancy(top, bottom)[run.left:run.right + 1]
        return float(np.mean([b.any() for b in np.array_split(occ, SPREAD_BINS)]))

    def full_row(self, top: int, bottom: int, run: Run) -> bool:
        """Some single row in [top, bottom] is inked in nearly every slice of
        `run`, by glyph strokes only. The fixed head slice of large type holds
        only ascenders, too sparse for HEAD_SPREAD, but a row through its
        x-height is inked from end to end; a row of labels never is, and a row
        that owes its spread to a drawn line (a box edge) is excluded by the
        stroke-length limit."""
        masked = self.mask[top:bottom + 1, run.left:run.right + 1]
        masked = masked & self.keep[None, run.left:run.right + 1]
        bins = np.array_split(np.arange(run.length), SPREAD_BINS)
        longest = GLYPH_STROKE_FRAC * self.width
        for row in masked:
            if max((b - a + 1 for a, b in _runs(row)), default=0) > longest:
                continue
            if np.mean([row[b].any() for b in bins if b.size]) >= FULL_ROW_SPREAD:
                return True
        return False

    def has_text_texture(self, band: Band) -> bool:
        """Ink density of type: present but sparse in nearly every slice.
        A plotted curve or a photograph is denser or emptier somewhere."""
        r = self.run(band.top, band.bottom)
        if r is None:
            return False
        block = self.mask[band.top:band.bottom + 1, r.left:r.right + 1]
        block = block & self.keep[None, r.left:r.right + 1]
        dens = np.array([b.mean() for b in np.array_split(block, SPREAD_BINS, axis=1) if b.size])
        return bool(dens.size) and float(np.mean((dens >= 0.01) & (dens <= 0.45))) >= 0.8

    def is_header(self, band: Band) -> bool:
        """A running header: one line of type that touches a crop edge and
        whose ink runs off it. The line of type itself may stop a word space
        short of the edge (the crop fell between two words), so its run is
        held to 1% of the width; the ink beyond it must reach the edge."""
        if band.height > self.text_max:
            return False
        r = self.run(band.top, band.bottom)
        if r is None or r.length < HEADER_FRAC * self.width:
            return False
        edge = 0.01 * self.width
        touches = r.left <= edge or r.right >= self.width - 1 - edge
        cols = np.flatnonzero(self.occupancy(band.top, band.bottom))
        off = max(2, round(HEADER_EDGE_FRAC * self.width))
        runs_off = cols[0] <= off or cols[-1] >= self.width - 1 - off
        return touches and runs_off and self.has_text_texture(band)

    def is_rule(self, band: Band) -> bool:
        """A page rule: thin, nearly solid, touching a crop edge, and running
        past the frame on both sides. The last condition keeps out the top
        edge of this plot's own box when it lines up with a neighbour's."""
        if band.height > max(2, RULE_MAX_FRAC * self.width):
            return False
        r = self.run(band.top, band.bottom)
        if r is None or r.length < WIDE_FRAC * self.width:
            return False
        occ = self.occupancy(band.top, band.bottom)[r.left:r.right + 1]
        x0, _, x1, _ = self.frame
        edge = EDGE_TOUCH_FRAC * self.width
        margin = OVERHANG_FRAC * self.width
        past = RULE_PAST_FRAC * self.width
        left_ok = r.left <= x0 - past or (r.left <= edge and x0 < margin)
        right_ok = r.right >= x1 + past or (
            r.right >= self.width - 1 - edge and x1 > self.width - 1 - margin
        )
        touches = r.left <= edge or r.right >= self.width - 1 - edge
        return (
            occ.mean() > 0.9
            and touches
            and left_ok
            and right_ok
            and self.overhangs(r.left, r.right)
        )

    def has_drawn_line(self, band: Band) -> bool:
        """Some row of the band holds an unbroken horizontal run of ink at
        least DRAWN_LINE_FRAC of the width: an x-axis, the top of a plot box
        or a rule, wherever it sits relative to this frame (a plot stacked
        off to one side, in a staggered stack, has its axis outside it)."""
        longest = DRAWN_LINE_FRAC * self.width
        masked = self.mask[band.top:band.bottom + 1] & self.keep[None, :]
        for row in masked:
            if row.sum() >= longest and any(b - a + 1 >= longest for a, b in _runs(row)):
                return True
        return False

    def line_run(self, band: Band) -> Run | None:
        """The band read as one line with word gaps up to LINE_GAP_FRAC
        closed: the glyph tops of a line the crop slices are too sparse for
        run(), which would read only one word of them."""
        return _longest_closed_run(
            self.occupancy(band.top, band.bottom), round(LINE_GAP_FRAC * self.width)
        )

    def is_solid(self, band: Band) -> bool:
        """Much of the band's line is columns inked over nearly all of its
        rows (see SOLID_COLUMN_FRAC)."""
        r = self.line_run(band)
        if r is None:
            return False
        block = self.mask[band.top:band.bottom + 1, r.left:r.right + 1]
        block = block & self.keep[None, r.left:r.right + 1]
        return float(np.mean(block.mean(0) >= 0.9)) >= SOLID_COLUMN_FRAC

    def panel_extent(self, bottom: int) -> tuple[int, int]:
        """Leftmost and rightmost column of the ink beside the frame, in rows
        from the frame's top down to `bottom`: the frame's own span widened
        over inked columns for as long as no blank stretch wider than
        PANEL_GAP_FRAC intervenes (the y-axis title, tick labels, a legend or
        a neighbouring panel set close by)."""
        x0, y0, x1, _ = self.frame
        occ = self.mask[y0:bottom].sum(0) >= 2
        gap = max(2, round(PANEL_GAP_FRAC * self.width))
        left, right = x0, x1
        while left > 0:
            cols = np.flatnonzero(occ[max(0, left - gap):left])
            if cols.size == 0:
                break
            left = max(0, left - gap) + int(cols[0])
        while right < self.width - 1:
            cols = np.flatnonzero(occ[right + 1:right + 1 + gap])
            if cols.size == 0:
                break
            right = right + 1 + int(cols[-1])
        return left, right

    def reaches_past_panel(self, band: Band) -> bool:
        """A caption line starts at the column margin and is set to the
        column's width, so it runs off the crop or reaches, on at least one
        side, as far out as the panel's outermost ink (panel_extent). A line
        set inside that extent on both sides is the panel's own: a subtitle,
        a note, a legend row centred under the axes."""
        line = self.line_run(band)
        if line is None:
            return False
        edge = EDGE_TOUCH_FRAC * self.width
        if line.left <= edge or line.right >= self.width - 1 - edge:
            return True
        left, right = self.panel_extent(band.top)
        slack = PANEL_REACH_FRAC * self.width
        return line.left <= left + slack or line.right >= right - slack

    def is_page_text(self, band: Band) -> bool:
        """What a bottom cut may remove below the caption's first line: more
        lines of type (no drawn line in them, not solid, at most two merged
        lines tall), a page rule or a lone thin line, a small mark, or what
        the crop's bottom edge slices: a sliver, or a line of type cut to its
        glyph tops. A photograph, a plot, a box edge or a dashed outline is
        figure content, whoever's panel it belongs to."""
        if self.is_rule(band):
            return True
        at_edge = band.bottom == self.height - 1
        if at_edge and band.height <= max(3, round(EDGE_SLIVER_FRAC * self.width)):
            return True
        r = self.run(band.top, band.bottom)
        if r is None:
            return True
        if band.height <= max(2, RULE_MAX_FRAC * self.width):
            occ = self.occupancy(band.top, band.bottom)
            if r.length >= WIDE_FRAC * self.width and occ[r.left:r.right + 1].mean() > 0.9 \
                    and occ.sum() - occ[r.left:r.right + 1].sum() <= self.noise:
                return True
        if self.has_drawn_line(band):
            return False
        if at_edge and self.is_cut_text(band):
            return True
        if self.is_solid(band):
            return False
        if band.height <= self.text_max and r.length <= SMALL_MARK_FRAC * self.width:
            return True
        return band.height <= 2 * self.text_max

    def is_stacked(self, band: Band) -> bool:
        """Read over the frame's own columns, the band falls apart into more
        than one line: page text above the panel merged, through ink outside
        those columns (a panel letter, a neighbour), with the panel's legend
        or title below it. Such a band is not one line of page text."""
        x0, _, x1, _ = self.frame
        sub = self.mask[band.top:band.bottom + 1, x0:x1 + 1] & self.keep[None, x0:x1 + 1]
        return len(_runs(sub.sum(1) > self.noise)) > 1

    def is_cut_text(self, band: Band) -> bool:
        """A text line sliced by the crop edge: only glyph tops or bottoms
        survive, too sparse for the closed-run test, but inked all the way
        across. Measured in fine bins over the frame's own span, where a row
        of tick labels, however dense, leaves every other bin empty."""
        if band.height > self.text_max:
            return False
        x0, _, x1, _ = self.frame
        occ = self.occupancy(band.top, band.bottom)
        cols = np.flatnonzero(occ)
        if cols.size == 0:
            return False
        coarse = np.mean([b.any() for b in np.array_split(occ, SPREAD_BINS)])
        fine = np.mean([b.any() for b in np.array_split(occ[x0:x1 + 1], FINE_BINS)])
        return (
            coarse >= EDGE_BIN_COVER
            and fine >= FINE_BIN_COVER
            and self.overhangs(int(cols[0]), int(cols[-1]))
        )

    def continues(self, line: Band, band: Band) -> bool:
        """`band` is the next line of the paragraph `line` belongs to: same
        height, same left margin, ordinary leading. Catches the short last
        line of body text above a panel, which is too narrow to be wide."""
        if abs(band.height - line.height) > 0.25 * line.height:
            return False
        if band.top - line.bottom - 1 > LEADING_FRAC * line.height:
            return False
        a = self.run(line.top, line.bottom)
        b = self.run(band.top, band.bottom)
        if a is None or b is None or b.length < CONTINUATION_FRAC * self.width:
            return False
        return abs(a.left - b.left) <= 0.01 * self.width

    def ends_paragraph(self, line: Band, nxt: Band) -> bool:
        """`line` is the short closing line of a paragraph of page text that
        `nxt` continues below: type of the same size, starting left of the
        frame where no part of the panel below its x-axis reaches, with all of
        its ink in that one run."""
        x0 = self.frame[0]
        if not 0.65 * nxt.height <= line.height <= 1.5 * nxt.height:
            return False
        if nxt.top - line.bottom - 1 > 4 * nxt.height:
            return False
        r = self.run(line.top, line.bottom)
        if r is None or r.length < CONTINUATION_FRAC * self.width:
            return False
        if r.left > x0 - OVERHANG_FRAC * self.width:
            return False
        occ = self.occupancy(line.top, line.bottom)
        outside = occ.sum() - occ[r.left:r.right + 1].sum()
        return outside <= 0.05 * occ.sum() and self.has_text_texture(line)


def _bottom_cut(page: Page) -> tuple[int, int | None]:
    """Row to cut the crop at, below the frame, and the index of the first
    band removed; (page.height, None) when no caption."""
    x0, _, x1, y1 = page.frame
    below = page.bands(
        y1 + 1, page.height,
        x0 - round(WINDOW_LEFT_FRAC * page.width),
        x1 + 1 + round(WINDOW_RIGHT_FRAC * page.width),
    )
    # The panel's own x-axis is never a caption candidate: the tick labels and
    # the axis title come first under the frame, and an axis title wider than
    # a narrow panel passes every caption test below. below[0] is often
    # neither of them but the rest of a box line drawn thicker than the 1-px
    # frame, with the outward tick marks; the tick labels are then below[1]
    # and the title below[2]. A band 0 that thin is read as ticks. When it
    # was in fact the tick labels of an axis with no title, the first caption
    # line is kept, which is the safe way to be wrong.
    ticks = bool(below) and below[0].height <= TICK_BAND_FRAC * page.width
    first = 3 if ticks else 2
    for i in range(first, len(below)):
        band, prev = below[i], below[i - 1]
        if page.is_rule(band):
            continue
        head = band.top + min(band.height, page.slice) - 1
        # The x-height test reads the top half of a band no taller than a line
        # or two of type; a taller band gets only the fixed head slice, so a
        # photograph further down it can never stand in for a line of text.
        one_line = band.height <= page.text_max
        half = head
        if one_line:
            half = band.top + max(min(band.height, page.slice), band.height // 2) - 1
        run = page.run(band.top, band.bottom)
        caption = (
            (
                page.is_wide_line(band)
                or (band.bottom == page.height - 1 and page.is_cut_text(band))
            )
            and (
                page.spread(band.top, head, run) >= HEAD_SPREAD
                or (one_line and page.full_row(band.top, half, run))
            )
            # A caption line is type: no drawn line anywhere in it (the axis
            # of a plot stacked off to one side), not a solid mark (a dashed
            # outline, the top of a photograph), and set out to the column
            # margin rather than inside the panel's own extent.
            and not page.has_drawn_line(band)
            and not page.is_solid(band)
            and page.reaches_past_panel(band)
        )
        if not caption:
            continue
        # Everything the cut removes must be page text too. A band of figure
        # content under the line (another plot of a stack the frame covers
        # only part of, a photograph, the rest of an outline) means the line
        # is inside the figure, not its caption; a later line may still be.
        if not all(page.is_page_text(b) for b in below[i + 1:]):
            continue
        cut = page.cut_row(prev, band.top)
        if cut is None:
            # Ink outside the window crosses this gap (a neighbour's panel
            # letter, a second text column): no cut here. A later caption
            # line may still have a clean gap above it.
            continue
        removed = i
        # The caption's first line is wide, but the crop may also hold the
        # short last line of the body-text paragraph that precedes it.
        if i - 1 >= first and page.ends_paragraph(prev, band):
            higher = page.cut_row(below[i - 2], prev.top)
            if higher is not None:
                cut, removed = higher, i - 1
        return cut, removed
    return page.height, None


# Evidence that a band above the frame is page furniture on its own. A single
# line of type ("text_line", "sliced_line") is not: a panel title or a legend
# row set above the axes is a wide, evenly inked line too.
_PAGE_FURNITURE = ("rule", "sliced_top", "header", "continues")


def _anchored(found: list[tuple[Band, str]], k: int) -> bool:
    """Whether band k of the top scan, and so everything above it, can be
    cut. A line of type counts as page text only when it follows, at
    ordinary leading, page text that counts on its own: a page rule, a line
    the crop's top edge slices, a running header, or a paragraph. A lone
    line directly above the frame, or one set apart from the text above it
    by more than leading, is kept as part of the panel."""
    band, label = found[k]
    if label in _PAGE_FURNITURE:
        return True
    if label == "edge_sliver" or k == 0:
        return False
    upper, upper_label = found[k - 1]
    if upper_label == "edge_sliver":
        return False
    if band.top - upper.bottom - 1 > LEADING_FRAC * band.height:
        return False
    return _anchored(found, k - 1)


def _top_cut(page: Page) -> tuple[int, tuple[str, ...]]:
    """Row to cut the crop at, above the frame, and why each band above it
    was taken for page text; (0, ()) when nothing is foreign."""
    _, y0, x1, _ = page.frame
    col1 = x1 + 1 + round(WINDOW_RIGHT_FRAC * page.width)
    above = page.bands(0, y0, 0, col1)
    prev_text = None
    # found[k] labels above[k]: the scan stops at the first band that is not
    # page text, so the two lists stay aligned.
    found: list[tuple[Band, str]] = []
    for j, band in enumerate(above):
        tail = band.bottom - min(band.height, page.slice) + 1
        # A band that is two lines stacked in the frame's own columns (page
        # text joined to the panel's legend or title through a panel letter
        # beside them) is not one line of page text, whatever it looks like
        # as a whole: only a rule or an edge sliver can be read from it.
        stacked = page.is_stacked(band)
        text_line = (
            not stacked
            and band.height <= page.text_max
            and page.is_wide_line(band)
            and page.spread(tail, band.bottom, page.run(band.top, band.bottom)) >= HEAD_SPREAD
            and page.has_text_texture(band)
        )
        if page.is_rule(band):
            label = "rule"
        elif stacked:
            if j == 0 and band.top == 0:
                label = "edge_sliver"
            else:
                break
        elif page.is_cut_text(band) and band.top == 0:
            label = "sliced_top"
        elif band.top <= HEADER_ZONE_FRAC * page.width and page.is_header(band):
            label = "header"
        elif text_line:
            label = "text_line"
        elif page.is_cut_text(band) and page.has_text_texture(band):
            # Not sliced by the crop: a line inked all the way across that
            # the closed-run test missed (wide word spaces).
            label = "sliced_line"
        elif prev_text is not None and page.continues(prev_text, band):
            label = "continues"
        elif j == 0 and band.top == 0:
            # A sliver at the very top edge is the cut-off bottom of whatever
            # was above the crop; it neither ends the scan nor is cut alone.
            label = "edge_sliver"
        else:
            break
        found.append((band, label))
        prev_text = band if text_line else None

    # Cut under the lowest page-text band that has a clean gap below it.
    top = 0
    for k in range(len(found) - 1, -1, -1):
        if not _anchored(found, k):
            continue
        nxt_top = above[k + 1].top if k + 1 < len(above) else y0
        cut = page.cut_row(found[k][0], nxt_top)
        if cut is not None:
            top = cut
            break
    labels = [label for band, label in found if band.bottom < top]

    # A page rule is page furniture and so is everything above it. Bands are
    # re-read down to just below the frame's top edge, because the pipeline
    # sometimes took a header rule for the top of an open plot: its frame top
    # then IS the rule, and the running header sits right above it.
    ruled = page.bands(0, min(page.height, y0 + 3), 0, col1)
    rules = [j for j, band in enumerate(ruled) if page.is_rule(band)]
    if rules:
        j = rules[-1]
        rule = ruled[j]
        cut = None
        if rule.bottom < y0 - 1:
            nxt_top = ruled[j + 1].top if j + 1 < len(ruled) else y0
            cut = page.cut_row(rule, nxt_top)
        if cut is None and j > 0:
            cut = page.cut_row(ruled[j - 1], rule.top, min_gap=RULE_MIN_GAP)
        if cut is not None and cut > top:
            top = cut
            labelled = {(band.top, band.bottom): label for band, label in found}
            labels = [
                "rule" if k == j else labelled.get((band.top, band.bottom), "above_rule")
                for k, band in enumerate(ruled)
                if band.bottom < cut
            ]
            if rule.bottom >= cut:
                labels.append("rule_at_frame")
    return top, tuple(labels)


def compute_trim(crop: np.ndarray, overlay: np.ndarray) -> Trim:
    """The box to publish crop and overlay at. Full-size unless every check
    above passed; see the module docstring."""
    height, width = crop.shape[:2]
    full = (0, 0, width, height)
    if overlay.shape[:2] != crop.shape[:2]:
        return Trim(None, full, False, False, "size_mismatch")
    frame = frame_from_overlay(overlay)
    if frame is None:
        return Trim(None, full, False, False, "no_frame")
    x0, y0, x1, y1 = frame
    if x1 - x0 < MIN_FRAME_FRAC * width or y1 - y0 < MIN_FRAME_FRAC * height:
        return Trim(frame, full, False, False, "frame_too_small")
    page = Page(crop, frame)
    bottom, bottom_band = _bottom_cut(page)
    top, top_evidence = _top_cut(page)
    # Whatever the scans decided, the frame itself stays whole.
    top = min(top, y0)
    bottom = max(bottom, y1 + 1)
    return Trim(
        frame,
        (0, top, width, bottom),
        top > 0,
        bottom < height,
        "ok",
        bottom_band if bottom < height else None,
        top_evidence if top > 0 else (),
    )


def trim_for_files(crop_path: Path, overlay_path: Path) -> Trim:
    """compute_trim for a figure directory's crop.png and overlay.png. A
    missing or unreadable overlay means no frame, so no trim."""
    with Image.open(crop_path) as image:
        crop = np.asarray(image.convert("RGB"))
    try:
        with Image.open(overlay_path) as image:
            overlay = np.asarray(image.convert("RGB"))
    except OSError:
        height, width = crop.shape[:2]
        return Trim(None, (0, 0, width, height), False, False, "no_overlay")
    return compute_trim(crop, overlay)
