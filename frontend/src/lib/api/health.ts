export type HealthStatus = {
  status: "ok";
  database: "connected";
};

export async function getHealth(
  baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  fetcher: typeof fetch = fetch,
): Promise<HealthStatus> {
  const response = await fetcher(`${baseUrl.replace(/\/$/, "")}/api/v1/health`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`健康检查失败：${response.status}`);
  }
  const data: unknown = await response.json();
  if (
    typeof data !== "object" ||
    data === null ||
    !("status" in data) ||
    !("database" in data) ||
    data.status !== "ok" ||
    data.database !== "connected"
  ) {
    throw new Error("健康检查返回了无效数据");
  }
  return { status: data.status, database: data.database };
}
