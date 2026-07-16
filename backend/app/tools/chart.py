import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings
from app.repositories.workspaces import validate_workspace_path
from app.runtime_profiles import RuntimeProfileService
from app.sandbox.runtime import SandboxError, SandboxRequest
from app.tools.base import Tool, ToolExecutionError, ToolResultEnvelope, ToolSpec


class ChartData(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=64)
    rows: list[list[Any]] = Field(min_length=1, max_length=10000)


class ChartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["1"] = "1"
    data: ChartData | None = None
    input_artifact_id: str | None = None
    input_workspace_path: str | None = Field(default=None, max_length=1000)
    chart_type: Literal["line", "bar", "scatter", "histogram", "box", "violin", "regression"]
    x: str
    y: list[str] = Field(min_length=1, max_length=16)
    title: str = Field(default="", max_length=240)
    backend: Literal["auto", "matplotlib", "seaborn", "echarts"] = "auto"
    outputs: list[Literal["png", "svg", "html"]] = Field(
        default_factory=lambda: ["png"], min_length=1, max_length=3
    )
    width: int = Field(default=1200, ge=320, le=4096)
    height: int = Field(default=720, ge=240, le=4096)

    @model_validator(mode="after")
    def validate_request(self):
        sources = sum(
            source is not None
            for source in (self.data, self.input_artifact_id, self.input_workspace_path)
        )
        if sources != 1:
            raise ValueError("Provide exactly one data source")
        if self.data:
            if any(len(row) != len(self.data.columns) for row in self.data.rows):
                raise ValueError("Every row must match columns")
            if self.x not in self.data.columns or any(
                item not in self.data.columns for item in self.y
            ):
                raise ValueError("Chart encoding references an unknown column")
            if any(len(str(value)) > 10000 for row in self.data.rows for value in row):
                raise ValueError("Chart value is too long")
        if "html" in self.outputs and self.backend not in {"auto", "echarts"}:
            raise ValueError("HTML output requires ECharts")
        return self


def select_backend(request: ChartRequest) -> tuple[str, str]:
    if request.backend != "auto":
        return request.backend, "explicit backend"
    if "html" in request.outputs:
        return "echarts", "interactive HTML requested"
    if request.chart_type in {"histogram", "box", "violin", "regression"}:
        return "seaborn", "statistical chart type"
    return "matplotlib", "deterministic static chart"


class ChartRenderTool(Tool):
    spec = ToolSpec(
        name="chart.render",
        version="1.0",
        description="Render a declarative chart with an isolated runtime",
        input_schema={
            "required": ["chart_type", "x", "y"],
            "type": "object",
            "properties": {
                "data": {"type": "object"},
                "input_workspace_path": {
                    "type": "string",
                    "description": (
                        "Relative CSV path in the current Task Workspace, such as test.csv"
                    ),
                },
                "chart_type": {"type": "string"},
                "x": {"type": "string"},
                "y": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string"},
                "backend": {"type": "string"},
                "outputs": {"type": "array", "items": {"type": "string"}},
            },
        },
        output_schema={"type": "object"},
        permission="sandboxed_compute",
        side_effect_level="artifact_write",
        capabilities=["sandboxed_compute", "artifact_write"],
        permissions=["sandboxed_compute", "temporary_compute", "workspace_read", "artifact_write"],
        risk="sandboxed",
        execution_backend="sandbox.remote",
        resource_profile={"network": "none", "workspace": "read_only"},
        artifact_behavior={"produces": ["chart_image", "chart_html", "chart_spec"]},
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        if context is None:
            raise ToolExecutionError("invalid_decision", "chart.render requires execution context")
        try:
            request = ChartRequest.model_validate(tool_input)
        except Exception as exc:
            raise ToolExecutionError("invalid_input", "Invalid chart request") from exc
        if request.input_artifact_id:
            raise ToolExecutionError("invalid_input", "Artifact data input is not enabled yet")
        if request.input_workspace_path:
            request = request.model_copy(
                update={
                    "data": self._load_workspace_csv(
                        request.input_workspace_path,
                        context.workspace_path,
                    ),
                    "input_workspace_path": None,
                }
            )
            try:
                request = ChartRequest.model_validate(request.model_dump(mode="json"))
            except Exception as exc:
                raise ToolExecutionError(
                    "invalid_input", "Workspace CSV does not match the chart encoding"
                ) from exc
        backend, reason = select_backend(request)
        workspace_mode = (
            context.workspace_mode
            if context.effect_plan is not None
            else "read_only"
            if context.workspace_path is not None
            else "none"
        )
        root = Path(tempfile.mkdtemp(prefix="astra-chart-"))
        input_dir, output_dir = root / "input", root / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        (input_dir / "request.json").write_text(
            json.dumps({**request.model_dump(mode="json"), "backend": backend}, ensure_ascii=False),
            encoding="utf-8",
        )
        profile = RuntimeProfileService(self.settings).read()
        template = profile.get("active_image", self.settings.sandbox_runtime_image)
        sandbox_request = SandboxRequest(
            template=template,
            command=["/opt/astra/bin/render"],
            input_dir=input_dir,
            output_dir=output_dir,
            wall_time_seconds=self.settings.sandbox_wall_time_seconds,
            secure=True,
            allow_internet_access=False,
            environment={"TZ": "UTC", "PYTHONHASHSEED": "0"},
            metadata={"tool": "chart.render", "backend": backend},
            workspace_dir=context.workspace_path if workspace_mode != "none" else None,
            workspace_mode=workspace_mode,
        )
        try:
            job, refs, _result = await context.sandbox_service.execute(
                sandbox_request,
                run_id=context.run_id,
                tool_call_id=context.tool_call_id,
                runtime_profile={
                    "backend": backend,
                    "image": template,
                    "lock_digest": profile.get(
                        "dependency_digest", self.settings.sandbox_runtime_lock_digest
                    ),
                    "trace_id": context.trace_id,
                    "workspace": workspace_mode,
                    "workspace_path": "/workspace",
                },
                resource_limits={
                    "wall_time_seconds": sandbox_request.wall_time_seconds,
                    "memory_mb": self.settings.sandbox_memory_mb,
                    "cpus": self.settings.sandbox_cpus,
                    "pids": self.settings.sandbox_pids,
                    "network": "none",
                },
            )
        except SandboxError as exc:
            raise ToolExecutionError(exc.category, exc.safe_message) from exc
        finally:
            shutil.rmtree(root, ignore_errors=True)
        return ToolResultEnvelope(
            data={"backend": backend, "selection_reason": reason, "sandbox_job_id": job.id},
            artifacts=refs,
        ).model_dump(mode="json")

    @staticmethod
    def _load_workspace_csv(
        relative_path: str,
        workspace_path: Path | None,
    ) -> ChartData:
        if workspace_path is None:
            raise ToolExecutionError(
                "sandbox_policy_violation", "chart.render requires a Task Workspace"
            )
        normalized = validate_workspace_path(relative_path)
        root = workspace_path.resolve(strict=True)
        try:
            path = (root / normalized).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ToolExecutionError("invalid_input", "Workspace CSV was not found") from exc
        if (
            not path.is_relative_to(root)
            or path.is_symlink()
            or not path.is_file()
            or path.suffix.lower() != ".csv"
        ):
            raise ToolExecutionError("sandbox_policy_violation", "Invalid Workspace CSV path")
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ToolExecutionError("invalid_input", "Workspace CSV is too large")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                columns = next(reader)
                if not columns or len(columns) > 64 or len(set(columns)) != len(columns):
                    raise ValueError("Invalid CSV columns")
                rows = []
                for index, row in enumerate(reader):
                    if index >= 10_000:
                        raise ValueError("Too many CSV rows")
                    if len(row) != len(columns):
                        raise ValueError("CSV row width mismatch")
                    rows.append([ChartRenderTool._coerce_csv_value(value) for value in row])
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            raise ToolExecutionError("invalid_input", "Workspace CSV is invalid") from exc
        if not rows:
            raise ToolExecutionError("invalid_input", "Workspace CSV has no data rows")
        return ChartData(columns=columns, rows=rows)

    @staticmethod
    def _coerce_csv_value(value: str) -> Any:
        stripped = value.strip()
        if stripped == "":
            return ""
        try:
            return int(stripped)
        except ValueError:
            try:
                return float(stripped)
            except ValueError:
                return value
