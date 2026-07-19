from pxrd_fetcher.text import (
    clean_numeric_token,
    extract_figure_label,
    has_two_theta_degree_label,
    keyword_score,
    normalize_axis_label_text,
    parse_numeric_ticks,
    parse_numeric_token,
)


def test_keyword_score_prefers_pxrd_language():
    strong = keyword_score("PXRD pattern with Bragg peaks from powder X-ray diffraction")
    weak = keyword_score("UV-vis and NMR spectra for characterization")
    assert strong > 0.7
    assert weak < 0.5


def test_extract_figure_label():
    assert extract_figure_label("Figure 3. PXRD pattern of COF-1") == "Figure 3"
    assert extract_figure_label("No label here") is None


def test_numeric_token_cleanup_and_parsing():
    assert clean_numeric_token("28.0o") == "28.00"
    assert parse_numeric_token("I2.5") == 12.5
    assert parse_numeric_token("abc") is None


def test_parse_numeric_ticks_ignores_invalid_entries():
    parsed = parse_numeric_ticks([("0", 10.0), ("l0.5", 20.0), ("abc", 40.0)])
    assert parsed == [(10.0, 0.0), (20.0, 10.5)]


def test_axis_label_detection_accepts_common_pxrd_forms():
    assert has_two_theta_degree_label("2θ (degree)")
    assert has_two_theta_degree_label("2 theta degree")
    assert has_two_theta_degree_label("20 (degree)")
    assert not has_two_theta_degree_label("Wavelength (nm)")


def test_normalize_axis_label_text_expands_theta_symbol():
    normalized = normalize_axis_label_text("2θ (degree)")
    assert "theta" in normalized
    assert "degree" in normalized
