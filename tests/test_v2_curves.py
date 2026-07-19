"""Tests for the shared computed-curve classifier (curves.curve_is_computed).

The scan (pipeline) and the replot tool both use this to drop model-derived
curves; the tricky part is sparing experimental look-alikes such as "calcined".
"""

from pxrd_fetcher.v2.curves import curve_is_computed


COMPUTED_STATES = [
    "simulated", "sim", "Simulated", "calculated", "calculated diffraction pattern",
    "calculated/refined", "pawley", "pawley refined", "Pawley-refined",
    "pawley refinement", "pawly refine", "refined", "rietveld",
    "difference", "differences", "difference curve", "residual", "ycal",
    "simulated slipped AA model",
]

EXPERIMENTAL_STATES = [
    "experimental", "", "observed", "raw", "powder", "as-synthesized",
    "calcined", "calcined sample", "1m hcl", "5m naoh treated",
    "scco₂ activated", "vacuum activated", "3d printed", "24 h", "5 d", "hs",
]


def test_computed_states_are_dropped():
    for s in COMPUTED_STATES:
        assert curve_is_computed(s), f"{s!r} should be computed"


def test_experimental_states_are_kept():
    for s in EXPERIMENTAL_STATES:
        assert not curve_is_computed(s), f"{s!r} should be kept"


def test_calcined_is_not_calculated():
    # The headline edge case: 'calcined' is an experimental sample state and
    # must never be confused with 'calculated'.
    assert not curve_is_computed("calcined")
    assert not curve_is_computed(None, label="TpPa calcined at 300C")


def test_label_only_simulation_markers():
    # sample_state empty, but the label betrays a simulated/calculated pattern.
    assert curve_is_computed(None, label="NH₂-MIL-125-Sim", material_name="NH₂-MIL-125")
    assert curve_is_computed(None, label="kgm simulated")
    assert curve_is_computed("", label="[orange] BBT-ACN COF-1 (simulated)")
    assert not curve_is_computed(None, label="Experimental", material_name="COF")
    assert not curve_is_computed(None, label="TCN-H (hs)")


def test_stacking_reference_patterns_are_computed():
    # AA/AB-stacking curves are simulated reference patterns for a layer-
    # packing model, not measured data — even when the state field itself
    # (rather than an explicit "simulated" qualifier) is the only signal.
    assert curve_is_computed("AA stacking")
    assert curve_is_computed("AA-Stacking")
    assert curve_is_computed("Eclipsed AA")
    assert curve_is_computed("staggered (AB)")
    assert curve_is_computed("DFT-derived AB stacking")
    assert curve_is_computed("modeled")
    assert curve_is_computed(None, label="AB stacking")          # state missing, label-only


def test_stacking_in_material_name_is_not_computed():
    # A real synthesized sample can be *named* after its stacking arrangement
    # (e.g. "AA stacking CTF-1") — that is the material, not a simulated
    # reference curve, so an explicit experimental state must win.
    assert not curve_is_computed(
        "experimental", label="Experiment AA", material_name="AA stacking CTF-1"
    )
