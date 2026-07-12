"""Build with: E2B_API_KEY=... python template.py"""
from e2b import Template

template = (Template().from_ubuntu_image("24.04")
    .apt_install(["curl", "fonts-noto-cjk", "nodejs", "npm"])
    .run_cmd("curl -LsSf https://astral.sh/uv/install.sh | sh")
    .copy(".", "/opt/astra/runtime")
    .run_cmd("cd /opt/astra/runtime && ~/.local/bin/uv sync --frozen --no-dev && npm ci --ignore-scripts && npx playwright install --with-deps chromium")
    .run_cmd("mkdir -p /opt/astra/bin && install -m 0555 /opt/astra/runtime/render /opt/astra/bin/render")
    .set_envs({"MPLBACKEND": "Agg", "TZ": "UTC", "PYTHONHASHSEED": "0"}))

if __name__ == "__main__":
    Template.build(template, alias="astra-data-viz", cpu_count=2, memory_mb=2048)
