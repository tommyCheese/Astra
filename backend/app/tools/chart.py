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
from app.tools.base import (
    Tool,
    ToolExecutionError,
    ToolResultEnvelope,
    ToolSpec,
    materialize_skill_inputs,
)


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

    @model_validator(mode="before")
    @classmethod
    def normalize_inline_data(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = value.get("data")
        normalized = cls._normalize_inline_data(data)
        if normalized is data:
            return value
        return {**value, "data": normalized}

    @staticmethod
    def _normalize_inline_data(data: Any) -> Any:
        records = _normalize_record_rows(data)
        return records if records is not None else _normalize_column_data(data)

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
                "data": {
                    "description": (
                        "Inline chart data. Prefer {columns: [name...], rows: [[value...], ...]}; "
                        "column-oriented {name: [value...]} and record rows [{name: value}] "
                        "are also accepted."
                    ),
                    "anyOf": [
                        {
                            "type": "object",
                            "required": ["columns", "rows"],
                            "properties": {
                                "columns": {"type": "array", "items": {"type": "string"}},
                                "rows": {
                                    "type": "array",
                                    "items": {"type": "array"},
                                },
                            },
                        },
                        {"type": "object"},
                        {"type": "array", "items": {"type": "object"}},
                        {"type": "null"},
                    ],
                },
                "input_artifact_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "input_workspace_path": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": (
                        "Relative CSV path in the current Task Workspace, such as test.csv"
                    ),
                },
                "version": {"type": "string", "enum": ["1"]},
                "chart_type": {
                    "type": "string",
                    "enum": ["line", "bar", "scatter", "histogram", "box", "violin", "regression"],
                },
                "x": {"type": "string"},
                "y": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "title": {"type": "string", "maxLength": 240},
                "backend": {
                    "type": "string",
                    "enum": ["auto", "matplotlib", "seaborn", "echarts"],
                },
                "outputs": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["png", "svg", "html"]},
                    "minItems": 1,
                    "maxItems": 3,
                },
                "width": {"type": "integer", "minimum": 320, "maximum": 4096},
                "height": {"type": "integer", "minimum": 240, "maximum": 4096},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        permission="sandboxed_compute",
        side_effect_level="artifact_write",
        task_capabilities=["data.visualize", "artifact.render"],
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
        try:
            skill_inputs = await materialize_skill_inputs(context, input_dir)
        except ValueError as exc:
            shutil.rmtree(root, ignore_errors=True)
            raise ToolExecutionError(
                "sandbox_policy_violation",
                "Skill sandbox inputs failed immutable binding validation",
            ) from exc
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
            metadata={
                "tool": "chart.render",
                "backend": backend,
                "skill_input_count": str(len(skill_inputs)),
            },
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
        ).model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _load_workspace_csv(
        relative_path: str,
        workspace_path: Path | None,
    ) -> ChartData:
        if workspace_path is None:
            raise ToolExecutionError(
                "sandbox_policy_violation", "chart.render requires a Task Workspace"
            )
        path = _validated_workspace_csv_path(relative_path, workspace_path)
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ToolExecutionError("invalid_input", "Workspace CSV is too large")
        try:
            columns, rows = _read_chart_csv(path)
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


def _normalize_record_rows(data: Any) -> dict | None:
    if not isinstance(data, list) or not data or not all(isinstance(row, dict) for row in data):
        return None
    columns = list(data[0])
    if not columns or not all(set(row) == set(columns) for row in data):
        return None
    return {"columns": columns, "rows": [[row[column] for column in columns] for row in data]}


def _normalize_column_data(data: Any) -> Any:
    if not _is_column_data(data):
        return data
    lengths = {len(column) for column in data.values()}
    if len(lengths) != 1 or lengths == {0}:
        return data
    columns = list(data)
    rows = [list(row) for row in zip(*(data[column] for column in columns), strict=True)]
    return {"columns": columns, "rows": rows}


def _is_column_data(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if "columns" in data or "rows" in data:
        return False
    return all(isinstance(column, list) for column in data.values())


def _validated_workspace_csv_path(relative_path: str, workspace_path: Path) -> Path:
    normalized = validate_workspace_path(relative_path)
    root = workspace_path.resolve(strict=True)
    try:
        path = (root / normalized).resolve(strict=True)
    except FileNotFoundError as error:
        raise ToolExecutionError("invalid_input", "Workspace CSV was not found") from error
    invalid_path = (
        not path.is_relative_to(root)
        or path.is_symlink()
        or not path.is_file()
        or path.suffix.lower() != ".csv"
    )
    if invalid_path:
        raise ToolExecutionError("sandbox_policy_violation", "Invalid Workspace CSV path")
    return path


def _read_chart_csv(path: Path) -> tuple[list[str], list[list[Any]]]:
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
    return columns, rows
