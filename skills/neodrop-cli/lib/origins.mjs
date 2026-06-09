// Neodrop 部署的 web origin（产品域）与 api origin（backend 域）解耦。
//
// 线上：web = https://neodrop.ai，api = https://api.neodrop.ai
// 本地 dev：web = http://localhost:4001，api = http://localhost:3001
//
// CLI 让用户显式传 --api；不传时按 web origin 启发式推断。self-host 用户默认
// 假设 backend 反代到 web 同域 /trpc/*，需要时用 --api 覆盖。

export function inferApiOrigin(webOrigin) {
  let parsed;
  try {
    parsed = new URL(webOrigin);
  } catch {
    return webOrigin.replace(/\/+$/, "");
  }
  const host = (parsed.hostname || "").toLowerCase();
  const port = parsed.port;
  const scheme = parsed.protocol.replace(/:$/, "");

  // 线上 neodrop.ai → api.neodrop.ai
  if (host === "neodrop.ai") {
    return `${scheme}://api.neodrop.ai`;
  }

  // 本地 dev：localhost:4001 / 127.0.0.1:4001 → 同 host 3001
  if ((host === "localhost" || host === "127.0.0.1") && port === "4001") {
    return `${scheme}://${host}:3001`;
  }

  // 其他（self-host 反代等）：默认与 web 同域，假设 /trpc/* 反代到 backend。
  return webOrigin.replace(/\/+$/, "");
}
