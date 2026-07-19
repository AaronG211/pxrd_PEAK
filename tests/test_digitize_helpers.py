from pxrd_fetcher.digitize import fit_axis_calibration


def test_fit_axis_calibration_maps_pixels_to_values():
    calibration = fit_axis_calibration([(50.0, 0.0), (350.0, 20.0)], source="test")

    assert calibration is not None
    assert round(calibration.to_value(200.0), 2) == 10.0
    assert calibration.tick_count == 2


def test_fit_axis_calibration_rejects_implausible_pxrd_x_axis_span():
    calibration = fit_axis_calibration(
        [
            (134.5, 5.0),
            (263.5, 109995.0),
            (341.8, 176772.0),
            (548.5, 352997.0),
            (639.3, 430432.0),
        ],
        source="ocr-x",
    )

    assert calibration is None
