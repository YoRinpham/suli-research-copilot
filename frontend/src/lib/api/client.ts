export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
// Default for ordinary API requests; report generation overrides this at its endpoint.
export const DEFAULT_REQUEST_TIMEOUT_MS = 240_000;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(message: string, code: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  baseUrl = API_BASE_URL,
  fetcher: typeof fetch = fetch,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("请求期限无效");
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  init.signal?.addEventListener("abort", forwardAbort, { once: true });
  if (init.signal?.aborted) controller.abort();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let timedOut = false;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetcher(`${baseUrl.replace(/\/$/, "")}/api/v1${path}`, {
          cache: "no-store", ...init, signal: controller.signal,
        });
        const body: unknown = await response.json().catch(() => null);
        if (!response.ok) {
          const error = body && typeof body === "object" && "error" in body ? body.error : null;
          if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
            throw new ApiError(error.message, "code" in error && typeof error.code === "string" ? error.code : "unknown", response.status);
          }
          throw new Error(`请求失败：${response.status}`);
        }
        return body as T;
      })(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          timedOut = true;
          controller.abort();
          reject(new ApiError("请求超时。请先刷新当前状态，再安全重试；服务器可能仍在处理。", "request_timeout", 0));
        }, timeoutMs);
      }),
    ]);
  } catch (cause) {
    if (timedOut) throw new ApiError("请求超时。请先刷新当前状态，再安全重试；服务器可能仍在处理。", "request_timeout", 0);
    throw cause;
  } finally {
    if (timer) clearTimeout(timer);
    init.signal?.removeEventListener("abort", forwardAbort);
  }
}
