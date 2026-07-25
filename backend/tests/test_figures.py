from app.eval.figures import figure_png_bytes, save_complementarity_figure

BY_DIM = {
    "completeness": {"rule_only": 30, "llm_only": 0, "both": 0},
    "validity": {"rule_only": 0, "llm_only": 12, "both": 0},
    "cross_table": {"rule_only": 10, "llm_only": 0, "both": 0},
}


def test_figure_png_bytes_returns_a_png():
    data = figure_png_bytes(BY_DIM, "test")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number
    assert len(data) > 1000


def test_save_writes_a_file(tmp_path):
    out = save_complementarity_figure(BY_DIM, tmp_path / "fig.png", "test")
    assert out.exists()
    assert out.stat().st_size > 1000


def test_handles_empty_dimensions():
    data = figure_png_bytes({}, "empty")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
