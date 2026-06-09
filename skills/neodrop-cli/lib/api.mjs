// tRPC 11 HTTP 调用最小封装（适配 backend 配置的 superjson transformer）。
//
// URL 形态：
//   - query:    GET  /trpc/<proc>?input=<urlencoded {json:<input>}>
//   - mutation: POST /trpc/<proc>  body = {json:<input>}
//
// 响应形态（superjson）：
//   - 成功：{ result: { data: { json: <T>, meta?: {...} } } }
//   - 失败：{ error: { json: { message, code, data: {...} } } }
//
// 入参 / 出参的 superjson `meta` 字段用于 Date 等非 JSON 类型还原，CLI 用不上
// （凭证 expiresAt 直接用 ISO 字符串），这里只取 `json` 字段。
//
// 零运行时依赖：用 Node 原生 fetch（Node 18+），不引第三方 HTTP 库。

export class ApiError extends Error {
  // tRPC 业务错误（HTTP >= 400 或 body.error 非空）。
  // code 来自 tRPC 的错误码（'UNAUTHORIZED' / 'NOT_FOUND' / 'BAD_REQUEST' 等），
  // CLI 上层可据此分流（如 401 提示重 login）。
  constructor(message, code = "", httpStatus = 0) {
    super(code ? `[${code}] ${message}` : message);
    this.name = "ApiError";
    this.code = code;
    this.httpStatus = httpStatus;
  }
}

function buildUrl(apiOrigin, proc, inputValue) {
  const base = `${apiOrigin.replace(/\/+$/, "")}/trpc/${proc}`;
  if (inputValue === undefined) return base;
  // superjson 入参 wrapper：{ json: <value> }
  const qs = new URLSearchParams({ input: JSON.stringify({ json: inputValue }) });
  return `${base}?${qs.toString()}`;
}

// Cloudflare WAF 看到默认 UA 会按 bot 拒（HTTP 403 + error code 1010）。给一个
// 老实的客户端身份——告诉 CF/origin「这是 neodrop-cli」，便于排查与白名单。
// 改 UA 不是为了伪装，是为了通过基础的 client fingerprint check。
const USER_AGENT = "neodrop-cli/1.0 (+https://github.com/DeepLangAI/neodrop-skills)";

async function doRequest({ method, url, token, body }) {
  const headers = {
    "content-type": "application/json",
    "user-agent": USER_AGENT,
    accept: "application/json",
  };
  if (token) headers.authorization = `Bearer ${token}`;

  // 一次 transparent retry——线上 Cloudflare/upstream 偶发 TLS-layer 抖动
  // （"EOF occurred in violation of protocol" 等）。mutation 也加 retry：tRPC
  // mutation 在网络抖动 + 业务层未提交时是幂等可重的（最多多签发一个 PAT/订阅，
  // 可接受代价）。注意 fetch 对 4xx/5xx 不抛错——那由 handleResponse 处理，
  // 这里只 retry 真正的网络层异常。
  let lastErr;
  for (let i = 0; i < 2; i++) {
    try {
      return await fetch(url, { method, headers, body, signal: AbortSignal.timeout(30000) });
    } catch (err) {
      lastErr = err;
    }
  }
  // Node fetch 把真正的原因藏在 err.cause（如 ECONNREFUSED / ENOTFOUND /
  // self-signed certificate）；err.message 通常只是笼统的 "fetch failed"。
  const cause = lastErr?.cause;
  const detail = cause?.code || cause?.message || lastErr?.message || String(lastErr);
  throw new Error(`连接失败：${detail}`);
}

async function handleResponse(res) {
  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new Error(`非 JSON 响应（HTTP ${res.status}）：${text.slice(0, 200)}`);
    }
  }

  if (res.status >= 400 || (body && body.error)) {
    const err = (body && body.error) || {};
    const errJson = err.json || {};
    const msg = errJson.message || err.message || `HTTP ${res.status}`;
    const code = (errJson.data && errJson.data.code) || err.code || "";
    throw new ApiError(msg, code, res.status);
  }

  // superjson 响应剥层
  return body.result.data.json;
}

export async function trpcQuery(opts, proc, inputValue) {
  const url = buildUrl(opts.apiOrigin, proc, inputValue);
  const res = await doRequest({ method: "GET", url, token: opts.token });
  return handleResponse(res);
}

export async function trpcMutation(opts, proc, inputValue) {
  const url = buildUrl(opts.apiOrigin, proc); // mutation 不走 query input
  // mutation 永远发 JSON body：input 为 undefined 时发 {"json": null}
  const body = JSON.stringify({ json: inputValue === undefined ? null : inputValue });
  const res = await doRequest({ method: "POST", url, token: opts.token, body });
  return handleResponse(res);
}
