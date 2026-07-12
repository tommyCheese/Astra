from pathlib import Path


RUNTIME = Path(__file__).parents[2] / "runtimes" / "data-viz"


def test_data_viz_template_has_reproducible_dependency_contract():
    assert (RUNTIME / "uv.lock").exists()
    assert (RUNTIME / "package-lock.json").exists()
    assert "uv sync --frozen" in (RUNTIME / "template.py").read_text()
    assert "npm ci --ignore-scripts" in (RUNTIME / "template.py").read_text()
    render = (RUNTIME / "render").read_text()
    assert "/input/request.json" in render
    assert "/opt/astra/runtime" in render


def test_data_viz_renderer_is_declarative_and_offline_at_job_time():
    sources = "\n".join((RUNTIME / name).read_text() for name in ("render", "python.py", "echarts.mjs"))
    assert "pip install" not in sources
    assert "npm install" not in sources
    assert "eval(" not in sources
    assert "Noto Sans CJK SC" in sources
    assert 'matplotlib.use("Agg")' in sources
