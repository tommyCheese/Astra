from app.sandbox.e2b_provider import E2BSandboxProvider
from app.sandbox.runtime import SandboxRequest, SandboxSupervisor


class FakeFiles:
    def __init__(self): self.values = {}
    def write(self, path, value): self.values[path] = value
    def list(self, path, depth=None): return [type("Entry", (), {"name": "chart.png", "path": "/output/chart.png", "type": "file", "is_dir": False})()]
    def read(self, path, format=None): return b"\x89PNG\r\n\x1a\nmock"


class FakeCommands:
    def __init__(self): self.calls = []
    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return type("Result", (), {"exit_code": 0, "stdout": "ok", "stderr": ""})()


class FakeSandbox:
    sandbox_id = "sbx-test"
    def __init__(self): self.files, self.commands, self.killed = FakeFiles(), FakeCommands(), False
    def get_metrics(self): return [{"cpu_used_pct": 1}]
    def kill(self): self.killed = True


async def test_e2b_provider_enforces_request_security_and_terminates(tmp_path):
    created = {}
    sandbox = FakeSandbox()
    def factory(**kwargs):
        created.update(kwargs)
        return sandbox
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "request.json").write_text("{}")
    request = SandboxRequest("astra-data-viz-v1", ["/opt/astra/bin/render"], input_dir, output_dir)
    result = await SandboxSupervisor(E2BSandboxProvider("secret", factory=factory)).run(request)
    assert created["secure"] is True
    assert created["allow_internet_access"] is False
    assert created["timeout"] == 40
    assert "/input/request.json" in sandbox.files.values
    assert (output_dir / "chart.png").exists()
    assert result.provider == "e2b" and result.template == "astra-data-viz-v1"
    assert sandbox.killed
