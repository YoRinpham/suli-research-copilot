"""Server-owned statistical registry. Every returned number comes from dataset rows."""

import math
import statistics
from typing import Any

from scipy import stats


class AnalysisError(ValueError):
    pass


ANALYSIS_ENGINE_VERSION = "m5-stats-v1"
METHODS = frozenset({"descriptive", "grouped_descriptive", "welch_t", "paired_t", "pearson", "spearman"})


def validate_spec_shape(columns: list[str], method: str, mapping: dict[str, str],
                        parameters: dict[str, Any]) -> None:
    if method not in METHODS:
        raise AnalysisError("Unknown analysis method")
    needed = {"outcome"}
    if method in {"grouped_descriptive", "welch_t", "paired_t"}:
        needed.add("group")
    if method in {"pearson", "spearman"}:
        needed.add("predictor")
    if method == "paired_t":
        needed.add("subject")
    if set(mapping) != needed or any(mapping[key] not in columns for key in needed) or len(set(mapping.values())) != len(mapping):
        raise AnalysisError("Analysis column mapping is invalid")
    allowed = {"missing_policy"}
    if method in {"welch_t", "paired_t"}:
        allowed |= {"groups", "confidence_level", "alpha"}
        labels = parameters.get("groups")
        if not isinstance(labels, list) or len(labels) != 2 or labels[0] == labels[1] or any(not isinstance(label, str) for label in labels):
            raise AnalysisError("Select exactly two distinct comparison groups")
        level = parameters.get("confidence_level")
        if level is not None and (not isinstance(level, (float, int)) or not 0 < level < 1):
            raise AnalysisError("Confidence level must be between 0 and 1")
    if set(parameters) - allowed:
        raise AnalysisError("Unexpected method parameters")
    alpha = parameters.get("alpha")
    if alpha is not None and (not isinstance(alpha, (float, int)) or not 0 < alpha < 1):
        raise AnalysisError("Alpha must be explicitly chosen between 0 and 1")
    if parameters.get("missing_policy", "reject") != "reject":
        raise AnalysisError("M5 requires explicit cleaning of missing values")


def validate_spec_profile(profile: dict[str, Any], method: str, mapping: dict[str, str],
                          parameters: dict[str, Any]) -> None:
    columns = {item["name"]: item for item in profile["columns"]}
    validate_spec_shape(list(columns), method, mapping, parameters)
    numeric_roles = {"outcome"} | ({"predictor"} if method in {"pearson", "spearman"} else set())
    for role in numeric_roles:
        item = columns[mapping[role]]
        if item["inferred_type"] != "numeric" or item["missing_count"]:
            raise AnalysisError(f"{role} must be complete and numeric; approve cleaning first")
    if "group" in mapping:
        item = columns[mapping["group"]]
        if item["missing_count"]:
            raise AnalysisError("Group column has missing values")
        if method in {"welch_t", "paired_t"} and item["unique_count"] != 2:
            raise AnalysisError("Two-group method requires exactly two observed groups")
    if "subject" in mapping and columns[mapping["subject"]]["missing_count"]:
        raise AnalysisError("Subject identifier has missing values")


def _numeric(value: str | None, name: str) -> float:
    if value is None:
        raise AnalysisError(f"Missing value in {name}; approve explicit cleaning first")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise AnalysisError(f"{name} must contain finite values")
    return result


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        raise AnalysisError("No usable observations")
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
            "standard_deviation": statistics.stdev(values) if len(values) > 1 else None,
            "min": min(values), "max": max(values)}


def _finite_tree(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise AnalysisError("Statistical output is non-finite")
    if isinstance(value, dict):
        for child in value.values():
            _finite_tree(child)
    if isinstance(value, list):
        for child in value:
            _finite_tree(child)
    return value


def run_analysis(rows: list[dict[str, str | None]], columns: list[str], method: str,
                 mapping: dict[str, str], parameters: dict[str, Any]) -> dict[str, Any]:
    validate_spec_shape(columns, method, mapping, parameters)
    outcome = mapping["outcome"]
    if method == "descriptive":
        if set(parameters) - {"missing_policy"}:
            raise AnalysisError("Unexpected method parameters")
        values = [_numeric(row[outcome], outcome) for row in rows]
        result = {"method": "descriptive_summary", **_summary(values), "excluded_count": 0}
    elif method == "grouped_descriptive":
        if set(parameters) - {"missing_policy"}:
            raise AnalysisError("Unexpected method parameters")
        group = mapping["group"]
        actual = {row[group] for row in rows}
        if None in actual or not actual:
            raise AnalysisError("Grouping values must be present")
        labels = sorted(actual)
        result = {"method": "grouped_descriptive_summary", "groups": {
            label: _summary([_numeric(row[outcome], outcome) for row in rows if row[group] == label])
            for label in labels}, "excluded_count": 0}
    elif method in {"welch_t", "paired_t"}:
        if set(parameters) - {"groups", "confidence_level", "alpha", "missing_policy"}:
            raise AnalysisError("Unexpected method parameters")
        labels = parameters.get("groups")
        if not isinstance(labels, list) or len(labels) != 2 or labels[0] == labels[1] or any(not isinstance(label, str) for label in labels):
            raise AnalysisError("Select exactly two distinct comparison groups")
        group = mapping["group"]
        actual = {row[group] for row in rows}
        if actual != set(labels):
            raise AnalysisError("Comparator groups do not match the dataset")
        level = parameters.get("confidence_level")
        if level is not None and (not isinstance(level, (float, int)) or not 0 < level < 1):
            raise AnalysisError("Confidence level must be between 0 and 1")
        if method == "welch_t":
            left = [_numeric(row[outcome], outcome) for row in rows if row[group] == labels[0]]
            right = [_numeric(row[outcome], outcome) for row in rows if row[group] == labels[1]]
            if len(left) < 2 or len(right) < 2:
                raise AnalysisError("Welch test needs at least two observations per group")
            v1, v2 = statistics.variance(left), statistics.variance(right)
            if v1 + v2 == 0:
                raise AnalysisError("Both groups are constant")
            statistic, p_value = stats.ttest_ind(left, right, equal_var=False)
            se2 = v1 / len(left) + v2 / len(right)
            df = se2 ** 2 / ((v1 / len(left)) ** 2 / (len(left) - 1) + (v2 / len(right)) ** 2 / (len(right) - 1))
            pooled = math.sqrt(((len(left) - 1) * v1 + (len(right) - 1) * v2) / (len(left) + len(right) - 2))
            difference = statistics.mean(left) - statistics.mean(right)
            result = {"method": "welch_independent_t_test", "groups": {labels[0]: _summary(left), labels[1]: _summary(right)},
                      "difference": difference, "test_statistic": float(statistic), "degrees_of_freedom": df,
                      "p_value": float(p_value), "effect_size": {"name": "cohens_d", "value": difference / pooled if pooled else None},
                      "excluded_count": 0}
            if level is not None:
                radius = float(stats.t.ppf((1 + level) / 2, df)) * math.sqrt(se2)
                result["confidence_interval"] = {"level": level, "lower": difference - radius, "upper": difference + radius}
        else:
            subject = mapping["subject"]
            pairs: dict[str, dict[str, float]] = {}
            for row in rows:
                identifier = row[subject]
                if identifier is None or row[group] is None:
                    raise AnalysisError("Paired test needs complete subject and condition values")
                values = pairs.setdefault(identifier, {})
                if row[group] in values:
                    raise AnalysisError("Duplicate subject-condition pair")
                values[row[group]] = _numeric(row[outcome], outcome)
            if len(pairs) < 2 or any(set(values) != set(labels) for values in pairs.values()):
                raise AnalysisError("Paired test needs at least two complete matched subjects")
            left = [pairs[key][labels[0]] for key in sorted(pairs)]
            right = [pairs[key][labels[1]] for key in sorted(pairs)]
            differences = [a - b for a, b in zip(left, right)]
            if statistics.stdev(differences) == 0:
                raise AnalysisError("Paired differences are constant")
            statistic, p_value = stats.ttest_rel(left, right)
            difference = statistics.mean(differences)
            result = {"method": "paired_t_test", "groups": {labels[0]: _summary(left), labels[1]: _summary(right)},
                      "n_pairs": len(pairs), "difference": difference, "test_statistic": float(statistic),
                      "degrees_of_freedom": len(pairs) - 1, "p_value": float(p_value),
                      "effect_size": {"name": "cohens_dz", "value": difference / statistics.stdev(differences)}, "excluded_count": 0}
            if level is not None:
                radius = float(stats.t.ppf((1 + level) / 2, len(pairs) - 1)) * statistics.stdev(differences) / math.sqrt(len(pairs))
                result["confidence_interval"] = {"level": level, "lower": difference - radius, "upper": difference + radius}
    else:
        if set(parameters) - {"missing_policy"}:
            raise AnalysisError("Unexpected method parameters")
        predictor = mapping["predictor"]
        left = [_numeric(row[outcome], outcome) for row in rows]
        right = [_numeric(row[predictor], predictor) for row in rows]
        if len(left) < 3 or len(set(left)) < 2 or len(set(right)) < 2:
            raise AnalysisError("Correlation needs at least three nonconstant pairs")
        statistic, p_value = stats.pearsonr(left, right) if method == "pearson" else stats.spearmanr(left, right)
        result = {"method": f"{method}_correlation", "n": len(left), "coefficient": float(statistic),
                  "p_value": float(p_value), "effect_size": {"name": "correlation_coefficient", "value": float(statistic)},
                  "excluded_count": 0}
    return _finite_tree(result)
