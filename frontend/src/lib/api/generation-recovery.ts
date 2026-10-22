import { ApiError } from "./client.ts";

export function recoverGenerationTimeout(
  cause: unknown,
  refresh: () => Promise<void>,
  onError: (message: string) => void,
): boolean {
  if (!(cause instanceof ApiError) || cause.code !== "request_timeout") return false;
  const warning = "请求超时，服务器可能仍在处理。已请求读取已保存状态；若结果尚未出现，请稍后刷新页面，不要重复生成。";
  onError(warning);
  void refresh().catch(() => onError(`${warning} 读取已保存状态失败，请检查服务后刷新页面。`));
  return true;
}
