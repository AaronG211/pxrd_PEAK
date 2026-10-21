"""How a curve in outputs/pxrd.db is labelled on the Open PXRD site.

The local database records the pipeline's own decision about each curve in two
columns written by tools/admit_axis_verified.py: curves.admission (clean,
axis_verified or excluded) and, for an excluded curve, curves.admission_reason.

Until 2026-10-21 the site published only the first two classes: 9,385 of the
11,445 curves the paper releases. It now publishes all 11,445, as the paper
does, and says per curve which class it is in. The reason a curve was once
held back travels with the curve instead of the curve being withheld.

    local admission, admission_reason      site admission      curves
    clean          -                       clean                7,713
    axis_verified  -                       axis_verified        1,672
    excluded       own_shape_flag          shape_flagged        1,339
    excluded       axis_single_method      axis_unconfirmed       435
    excluded       axis_arbitrated         axis_unconfirmed        82
    excluded       model_derived           model_derived          204

  clean             the curve and every other curve in its figure passed the
                    offline shape checks. The frozen in_clean_set tier.
  axis_verified     unflagged itself; a sibling in the panel was flagged, and
                    the figure's 2-theta axis was cross-validated by two fits.
  shape_flagged     this curve's own trace failed the offline shape checks.
  axis_unconfirmed  unflagged itself; a sibling in the panel was flagged, and
                    the figure's axis was NOT cross-validated by two methods.
  model_derived     a label audit after extraction says this trace is a
                    simulated, refined or difference curve that escaped the
                    keyword filter.

This module is the one place the mapping is written down. The importer, the
bulk exporter and both metadata backfills import it, so the curves the site
publishes, the curves the release contains and the papers the backfills cover
cannot disagree.

in_clean_set is never redefined. It stays the 7,713-curve set the paper's
validation section and outputs/analysis/*.json are computed over, and the
Supabase CHECK `in_clean_set = (admission = 'clean')` keeps holding because
`clean` maps only to `clean`.

A local pair that is not in the table is refused, never guessed.
tools/admit_axis_verified.py can also write axis_unreadable or
axis_agreement_too_wide; the first appears only when that tool ran with the
assets volume detached, and publishing it under any label would be publishing
the state of a machine rather than of the data.
"""

from __future__ import annotations

import sqlite3

SITE_ADMISSIONS = (
    "clean",
    "axis_verified",
    "shape_flagged",
    "axis_unconfirmed",
    "model_derived",
)

LOCAL_TO_SITE: dict[tuple[str, str | None], str] = {
    ("clean", None): "clean",
    ("axis_verified", None): "axis_verified",
    ("excluded", "own_shape_flag"): "shape_flagged",
    ("excluded", "axis_single_method"): "axis_unconfirmed",
    ("excluded", "axis_arbitrated"): "axis_unconfirmed",
    ("excluded", "model_derived"): "model_derived",
}

# A database written before tools/admit_axis_verified.py has no admission
# columns. It publishes the original clean set, which is what it means.
LEGACY_PUBLISHED = "{alias}.in_clean_set=1"


def site_admission(admission: str | None, reason: str | None) -> str | None:
    """The site's label for one local curve, or None when the pair is unknown."""
    return LOCAL_TO_SITE.get((admission or "", (reason or "").strip() or None))


def has_admission(conn: sqlite3.Connection) -> bool:
    """Whether curves carries both admission columns.

    One without the other is a half-applied tools/admit_axis_verified.py run,
    and neither reading of it would be honest, so it is refused.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(curves)")}
    present = {"admission", "admission_reason"} & columns
    if present and len(present) != 2:
        raise SystemExit(
            f"curves has {sorted(present)} but not both admission columns. "
            "Rerun tools/admit_axis_verified.py --apply."
        )
    return bool(present)


def site_admission_sql(alias: str = "c") -> str:
    """A SQL CASE giving the site label of `alias`, NULL for an unknown pair.

    Generated from LOCAL_TO_SITE so the SQL and the Python reading cannot drift.
    """
    whens = []
    for (admission, reason), site in LOCAL_TO_SITE.items():
        reason_test = (
            f"coalesce({alias}.admission_reason, '') = ''"
            if reason is None
            else f"{alias}.admission_reason = '{reason}'"
        )
        whens.append(
            f"when {alias}.admission = '{admission}' and {reason_test} then '{site}'"
        )
    return "case " + " ".join(whens) + " end"


def published_predicate(conn: sqlite3.Connection, alias: str = "c") -> str:
    """SQL for the `curves {alias}` rows the site publishes."""
    if not has_admission(conn):
        return LEGACY_PUBLISHED.format(alias=alias)
    return f"({site_admission_sql(alias)}) is not null"


def require_all_mapped(conn: sqlite3.Connection) -> None:
    """Refuse a database whose admission columns this module cannot read.

    Two checks. Every (admission, admission_reason) pair must be in
    LOCAL_TO_SITE, so nothing is published under a guessed label or dropped
    silently. And in_clean_set must agree with admission = 'clean', because the
    Supabase CHECK requires it and because the 7,713-curve clean set is a number
    the paper prints.
    """
    if not has_admission(conn):
        return
    unknown = conn.execute(
        f"""
        select c.admission, c.admission_reason, count(*)
        from curves c
        where ({site_admission_sql("c")}) is null
        group by 1, 2
        order by 3 desc
        """
    ).fetchall()
    if unknown:
        listed = ", ".join(f"{a!r}/{r!r} x{n}" for a, r, n in unknown)
        raise SystemExit(
            f"curves carries admission values site_admission.py does not map: {listed}. "
            "Refusing to publish them under a guessed label. Extend LOCAL_TO_SITE "
            "deliberately, or rerun tools/admit_axis_verified.py with the assets "
            "volume attached."
        )
    disagree = conn.execute(
        "select count(*) from curves "
        "where (admission = 'clean') != (coalesce(in_clean_set, 0) = 1)"
    ).fetchone()[0]
    if disagree:
        raise SystemExit(
            f"{disagree} curves disagree between admission='clean' and in_clean_set=1. "
            "in_clean_set is the frozen clean set; refusing to publish a corpus "
            "where the two readings differ."
        )
