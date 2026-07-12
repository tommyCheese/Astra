#!/usr/bin/env python3
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


request = json.loads(Path("/input/request.json").read_text(encoding="utf-8"))
data = request["data"]
frame = pd.DataFrame(data["rows"], columns=data["columns"])
backend = request["backend"]
kind = request["chart_type"]
plt.rcParams["font.family"] = ["Noto Sans CJK SC", "DejaVu Sans"]
figure, axis = plt.subplots(figsize=(request["width"] / 100, request["height"] / 100), dpi=100)
for y in request["y"]:
    if backend == "seaborn" and kind == "regression":
        sns.regplot(data=frame, x=request["x"], y=y, ax=axis, label=y)
    elif backend == "seaborn" and kind in {"histogram", "box", "violin"}:
        {"histogram": sns.histplot, "box": sns.boxplot, "violin": sns.violinplot}[kind](data=frame, x=request["x"], y=None if kind == "histogram" else y, ax=axis)
    elif kind == "bar":
        axis.bar(frame[request["x"]], frame[y], label=y)
    elif kind == "scatter":
        axis.scatter(frame[request["x"]], frame[y], label=y)
    else:
        axis.plot(frame[request["x"]], frame[y], label=y)
axis.set_title(request.get("title", ""))
if len(request["y"]) > 1:
    axis.legend()
figure.tight_layout()
for output in request["outputs"]:
    if output in {"png", "svg"}:
        figure.savefig(f"/output/chart.{output}")
Path("/output/chart-spec.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
