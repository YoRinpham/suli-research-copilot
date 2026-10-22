"""Deterministic chart registry rendering only persisted data columns."""

import io
import math
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "research-copilot-mpl"))
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib import font_manager
from matplotlib.ft2font import FT2Font


class FigureError(ValueError):
    pass


RENDERER_VERSION = "m5-matplotlib-v2"
CHARTS = frozenset({"histogram", "box_plot", "scatter", "group_summary"})


def _label_font(text: str) -> font_manager.FontProperties:
    required = {ord(character) for character in text if not character.isspace()}
    for family in ("DejaVu Sans", "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                   "WenQuanYi Zen Hei", "PingFang SC", "Arial Unicode MS"):
        try:
            path = font_manager.findfont(font_manager.FontProperties(family=family), fallback_to_default=False)
            if required <= FT2Font(path).get_charmap().keys():
                return font_manager.FontProperties(fname=path)
        except (ValueError, OSError):
            continue
    raise FigureError("No installed font can render every figure label")


def _numbers(rows: list[dict[str, str | None]], name: str) -> list[float]:
    try:
        values = [float(row[name]) for row in rows]
    except (ValueError, TypeError, KeyError) as exc:
        raise FigureError(f"{name} must be complete and numeric") from exc
    if not all(math.isfinite(value) for value in values):
        raise FigureError("Figure data must be finite")
    return values


def render_png(rows: list[dict[str, str | None]], columns: list[str], chart_type: str,
               x_column: str | None, y_column: str | None, group_column: str | None,
               title: str, options: dict[str, object]) -> bytes:
    if chart_type not in CHARTS:
        raise FigureError("Unknown chart type")
    if any(name is not None and name not in columns for name in (x_column, y_column, group_column)):
        raise FigureError("Figure refers to an unknown column")
    if not title.strip() or len(title) > 255 or not rows:
        raise FigureError("Figure title or dataset is invalid")
    if set(options) - {"bins"} or ("bins" in options and (chart_type != "histogram" or
       not isinstance(options["bins"], int) or not 1 <= options["bins"] <= 100)):
        raise FigureError("Figure options are invalid")
    figure = Figure(figsize=(6, 4), dpi=120)
    axes = figure.subplots()
    if chart_type == "histogram":
        if x_column is None or y_column is not None or group_column is not None:
            raise FigureError("Histogram needs one numeric column")
        axes.hist(_numbers(rows, x_column), bins=options.get("bins", 10), color="#2563a7")
        axes.set_xlabel(x_column)
        axes.set_ylabel("Count")
    elif chart_type == "scatter":
        if x_column is None or y_column is None or group_column is not None:
            raise FigureError("Scatter plot needs two numeric columns")
        axes.scatter(_numbers(rows, x_column), _numbers(rows, y_column), color="#2563a7")
        axes.set_xlabel(x_column)
        axes.set_ylabel(y_column)
    else:
        if y_column is None or group_column is None or x_column is not None:
            raise FigureError("Grouped plot needs a numeric y column and group column")
        labels = sorted({row[group_column] for row in rows if row[group_column] is not None})
        if not labels or any(row[group_column] is None for row in rows) or len(labels) > 20:
            raise FigureError("Group labels must be present and bounded")
        try:
            groups = [[float(row[y_column]) for row in rows if row[group_column] == label] for label in labels]
        except (ValueError, TypeError, KeyError) as exc:
            raise FigureError("Grouped figure needs complete numeric values") from exc
        if not all(math.isfinite(value) for group in groups for value in group):
            raise FigureError("Figure data must be finite")
        if chart_type == "box_plot":
            axes.boxplot(groups, tick_labels=labels)
        else:
            axes.bar(labels, [sum(group) / len(group) for group in groups], color="#2563a7")
        axes.set_xlabel(group_column)
        axes.set_ylabel(y_column)
    axes.set_title(title)
    labels = [axes.title, axes.xaxis.label, axes.yaxis.label,
              *axes.get_xticklabels(), *axes.get_yticklabels()]
    font = _label_font("".join(label.get_text() for label in labels))
    for label in labels:
        size = label.get_fontsize()
        label.set_fontproperties(font)
        label.set_fontsize(size)
    figure.tight_layout()
    output = io.BytesIO()
    figure.savefig(output, format="png", metadata={"Software": RENDERER_VERSION})
    return output.getvalue()
