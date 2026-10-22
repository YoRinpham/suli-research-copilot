import { ApiError } from "./client.ts";

type Gate = { active: boolean };

export async function runReportAction(
  gate: Gate,
  action: () => Promise<unknown>,
  refresh: () => Promise<unknown>,
  setBusy: (busy: boolean) => void,
  onSuccess: () => void,
  onError: (message: string) => void,
): Promise<void> {
  if (gate.active) return;
  gate.active = true;
  setBusy(true);
  try {
    await action();
    await refresh();
    onSuccess();
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === "request_timeout") {
      // Read back server state without extending the visible busy period.
      void refresh().catch(() => undefined);
    }
    onError(cause instanceof ApiError ? `${cause.code}：${cause.message}` :
      cause instanceof Error ? cause.message : "操作失败");
  } finally {
    gate.active = false;
    setBusy(false);
  }
}
