"""Bounded CSV ingestion, profiling and whitelist-only deterministic cleaning."""

import csv
import io
import math
import statistics
from collections import Counter
from typing import Any


class CSVError(ValueError):
    pass


DATA_ENGINE_VERSION = "m5-csv-v1"
ALLOWED_OPERATIONS = frozenset({"drop_exact_duplicates", "cast_numeric", "cast_categorical", "trim_whitespace", "normalize_missing", "drop_missing"})


def parse_csv(payload: bytes, max_rows: int, max_columns: int) -> tuple[list[dict[str, str | None]], list[str]]:
    try:
        stream = io.StringIO(payload.decode("utf-8-sig"), newline="")
        reader = csv.reader(stream, strict=True)
        columns = next(reader)
        if not columns or any(not name.strip() for name in columns) or len(columns) != len(set(columns)):
            raise CSVError("CSV headers must be nonempty and unique")
        if len(columns) > max_columns:
            raise CSVError("CSV exceeds configured column limit")
        rows: list[dict[str, str | None]] = []
        for values in reader:
            if len(values) != len(columns):
                raise CSVError("CSV row has the wrong number of columns")
            for value in values:
                if value:
                    try:
                        parsed = float(value)
                    except ValueError:
                        pass
                    else:
                        if not math.isfinite(parsed):
                            raise CSVError("CSV contains a non-finite numeric value")
            rows.append(dict(zip(columns, (value if value != "" else None for value in values))))
            if len(rows) > max_rows:
                raise CSVError("CSV exceeds configured row limit")
        if not rows:
            raise CSVError("CSV has no data rows")
        return rows, columns
    except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise CSVError("CSV is empty, malformed, or not UTF-8") from exc


def serialize_csv(rows: list[dict[str, str | None]], columns: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _finite_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CSVError("Expected a numeric value") from exc
    if not math.isfinite(number):
        raise CSVError("Non-finite numeric values are unsupported")
    return number


def profile_rows(rows: list[dict[str, str | None]], columns: list[str]) -> dict[str, Any]:
    summary: list[dict[str, Any]] = []
    for name in columns:
        present = [row[name] for row in rows if row[name] is not None]
        numeric: list[float] = []
        is_numeric = bool(present)
        for value in present:
            try:
                number = _finite_number(value)
                assert number is not None
                numeric.append(number)
            except CSVError:
                is_numeric = False
                break
        item: dict[str, Any] = {
            "name": name, "inferred_type": "numeric" if is_numeric else "categorical",
            "missing_count": len(rows) - len(present),
            "missing_fraction": (len(rows) - len(present)) / len(rows),
            "unique_count": len(set(present)),
        }
        if is_numeric:
            try:
                mean = statistics.mean(numeric)
                median = statistics.median(numeric)
                deviation = statistics.stdev(numeric) if len(numeric) > 1 else None
            except (OverflowError, ValueError) as exc:
                raise CSVError("Numeric values exceed the supported summary range") from exc
            if not all(math.isfinite(value) for value in (mean, median) if value is not None) or (
                    deviation is not None and not math.isfinite(deviation)):
                raise CSVError("Numeric values exceed the supported summary range")
            item.update(min=min(numeric), max=max(numeric), mean=mean,
                        median=median, standard_deviation=deviation)
        else:
            # Bounded category labels come from cells and may reach the model;
            # complete rows are not included in the profile.
            item["categories"] = [{"value": value[:100], "count": count} for value, count in
                                   sorted(Counter(present).items(), key=lambda pair: (-pair[1], pair[0]))[:10]]
        summary.append(item)
    return {"row_count": len(rows), "column_count": len(columns), "columns": summary,
            "engine_version": DATA_ENGINE_VERSION}


def validate_operations(columns: list[str], operations: list[dict[str, Any]]) -> None:
    if not isinstance(operations, list) or len(operations) > 20:
        raise CSVError("Cleaning plan must contain at most 20 operations")
    for operation in operations:
        if not isinstance(operation, dict):
            raise CSVError("Cleaning operation must be an object")
        kind = operation.get("op")
        if not isinstance(kind, str) or kind not in ALLOWED_OPERATIONS:
            raise CSVError("Unsupported cleaning operation")
        allowed_keys = {"op"}
        if kind in {"cast_numeric", "cast_categorical"}:
            allowed_keys.add("column")
            selected = [operation.get("column")]
        elif kind in {"trim_whitespace", "normalize_missing", "drop_missing"}:
            allowed_keys.add("columns")
            selected = operation.get("columns")
            if (not isinstance(selected, list) or not selected or
                    any(not isinstance(name, str) for name in selected) or
                    len(selected) != len(set(selected))):
                raise CSVError("Cleaning operation needs distinct named columns")
            if kind == "normalize_missing":
                allowed_keys.add("tokens")
                tokens = operation.get("tokens")
                if not isinstance(tokens, list) or not tokens or any(not isinstance(value, str) for value in tokens):
                    raise CSVError("Missing-value tokens must be explicit strings")
        else:
            selected = []
        if (set(operation) != allowed_keys or
                any(not isinstance(name, str) or name not in columns for name in selected)):
            raise CSVError("Invalid cleaning operation fields or column")


def clean_rows(rows: list[dict[str, str | None]], columns: list[str],
               operations: list[dict[str, Any]]) -> tuple[list[dict[str, str | None]], list[dict[str, Any]]]:
    validate_operations(columns, operations)
    current = [row.copy() for row in rows]
    effects: list[dict[str, Any]] = []
    for operation in operations:
        kind = operation["op"]
        selected = [operation["column"]] if kind in {"cast_numeric", "cast_categorical"} else operation.get("columns", [])
        tokens = operation.get("tokens", [])
        before = len(current)
        if kind == "drop_exact_duplicates":
            seen: set[tuple[str | None, ...]] = set()
            unique = []
            for row in current:
                key = tuple(row[name] for name in columns)
                if key not in seen:
                    seen.add(key)
                    unique.append(row)
            current = unique
        elif kind == "drop_missing":
            current = [row for row in current if all(row[name] is not None for name in selected)]
        else:
            for row in current:
                for name in selected:
                    value = row[name]
                    if value is None:
                        continue
                    if kind == "trim_whitespace":
                        row[name] = value.strip() or None
                    elif kind == "normalize_missing":
                        row[name] = None if value in tokens else value
                    elif kind == "cast_numeric":
                        row[name] = format(_finite_number(value), ".17g")
                    elif kind == "cast_categorical":
                        row[name] = str(value)
        effects.append({"operation": operation, "rows_before": before, "rows_after": len(current),
                        "rows_removed": before - len(current)})
    if not current:
        raise CSVError("Cleaning removed every row")
    return current, effects
