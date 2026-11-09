import { vi } from "vitest";

type Handler = (url: string, init?: RequestInit) => unknown;

/** Route fetch calls by "METHOD path" prefix; unmatched calls reject. Returns the call log. */
export function mockFetch(routes: Record<string, Handler | unknown>) {
  const calls: Array<{ method: string; url: string; body?: unknown }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const key = Object.keys(routes)
      .filter((k) => { const [m, p] = k.split(" "); return m === method && url.startsWith(`/api${p}`); })
      .sort((a, b) => b.length - a.length)[0];
    if (!key) return new Response(JSON.stringify({ detail: `no mock for ${method} ${url}` }), { status: 404 });
    const v = routes[key];
    const body = typeof v === "function" ? (v as Handler)(url, init) : v;
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}
