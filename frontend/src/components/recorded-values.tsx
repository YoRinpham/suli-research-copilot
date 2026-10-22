const labels: Record<string, string> = {
  method: "统计方法", groups: "分组结果", n: "样本数", count: "数量", mean: "均值", median: "中位数",
  standard_deviation: "标准差", min: "最小值", max: "最大值", difference: "组间差值", test_statistic: "检验统计量",
  degrees_of_freedom: "自由度", p_value: "p 值", effect_size: "效应量", name: "名称", value: "记录值",
  excluded_count: "排除数量", missing_count: "缺失数量", correlation: "相关系数", columns: "各列结果",
  missing_policy: "缺失值处理", outcome: "结果列", group: "分组列", predictor: "预测列", subject: "配对标识列",
};

function RecordedValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">未记录</span>;
  if (Array.isArray(value)) return <ul className="recorded-list">{value.map((entry, index) => <li key={index}><RecordedValue value={entry} /></li>)}</ul>;
  if (typeof value === "object") return <dl className="recorded-details">{Object.entries(value).map(([key, entry]) => <div key={key}><dt>{labels[key] ?? key}</dt><dd><RecordedValue value={entry} /></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

/** Display every stored value without rounding, recalculation or AI interpretation. */
export function RecordedValues({ values }: { values: Record<string, unknown> }) {
  return <div className="m5-table-wrap"><table className="m5-table recorded-values"><thead><tr><th scope="col">指标</th><th scope="col">记录值</th></tr></thead><tbody>{Object.entries(values).map(([key, value]) => <tr key={key}><th scope="row">{labels[key] ?? key}</th><td><RecordedValue value={value} /></td></tr>)}</tbody></table></div>;
}
