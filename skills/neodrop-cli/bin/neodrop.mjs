#!/usr/bin/env node
// neodrop CLI 入口。AI agent 与人类共用——直接打 Neodrop tRPC HTTP 接口
// （Bearer PAT 鉴权），不走 MCP。
//
// 调用：
//   npx neodrop <command> [args...]
//   或全局安装后：neodrop <command> [args...]
//
// 输出：
//   stdout = JSON（AI 直接 JSON.parse）；--pretty 切缩进 JSON 给人看
//   stderr = 日志 / 进度 / 错误描述
// 退出码：0 成功 / 1 业务错误 / 2 参数错误

import { hostname } from "node:os";
import { parseArgs } from "node:util";
import { ApiError, trpcMutation, trpcQuery } from "../lib/api.mjs";
import {
  clearCredentials,
  credentialsPath,
  readCredentials,
  requireCredentials,
  writeCredentials,
} from "../lib/credentials.mjs";
import { defaultSkillDest, installSkill } from "../lib/install-skill.mjs";
import { inferApiOrigin } from "../lib/origins.mjs";
import { emit, note, setPretty } from "../lib/output.mjs";
import { channelUrl, postUrl, userUrl } from "../lib/web-urls.mjs";

const DEFAULT_SERVER = process.env.NEODROP_SERVER || "https://neodrop.ai";
const ENV_API_OVERRIDE = process.env.NEODROP_API;

// 用法错误 → 退出码 2（区别于业务错误 1）。
class UsageError extends Error {}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 客户端标识——授权页和 settings/cli-tokens 上显示给用户辨认。
function detectClientName() {
  const host = hostname() || "host";
  const term = process.env.TERM_PROGRAM || "";
  if (process.env.CLAUDECODE) return `Claude Code @ ${host}`;
  if (process.env.CURSOR_TRACE_ID) return `Cursor @ ${host}`;
  if (term === "vscode") return `VS Code @ ${host}`;
  if (term === "WarpTerminal") return `Warp @ ${host}`;
  return `neodrop-cli @ ${host}`;
}

function authedCtx() {
  const creds = requireCredentials();
  return { apiOrigin: creds.apiOrigin, token: creds.token, creds };
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf-8");
}

// `--json '<input>'` / `--stdin` 二选一（互斥，调用前已校验），都没给则返回 undefined。
async function loadInput(values) {
  if (values.json) {
    try {
      return JSON.parse(values.json);
    } catch (err) {
      throw new UsageError(`--json 解析失败：${err.message}`);
    }
  }
  if (values.stdin) {
    return JSON.parse(await readStdin());
  }
  return undefined;
}

// parseArgs 包装：未知 flag / 缺值统一转成 UsageError（退出码 2）。
function parse(argv, options) {
  try {
    return parseArgs({ args: argv, options, allowPositionals: true, strict: true });
  } catch (err) {
    throw new UsageError(err.message);
  }
}

function toLimit(value) {
  if (value === undefined) return undefined;
  const n = Number(value);
  if (!Number.isFinite(n)) throw new UsageError(`--limit 必须是数字，收到「${value}」`);
  return n;
}

function requirePositional(positionals, index, hint) {
  const value = positionals[index];
  if (value === undefined) throw new UsageError(hint);
  return value;
}

// ---- 元命令 -----------------------------------------------------------

// 统一登录：session polling 模式。
//   1. startSession 拿到 sessionId + pollSecret + verification URL
//      （pollSecret 只在本进程内持有，是 poll 领 token 的唯一凭据，不进 URL）
//   2. 打印 URL 给用户复制到浏览器（不自动拉起，不开本地 server，无 callback）
//   3. 带 pollSecret 轮询 pollSession 直到 APPROVED / DENIED / EXPIRED
//   4. 拿到 token 写入 ~/.neodrop/credentials.json
async function cmdLogin(argv) {
  const { values } = parse(argv, {
    server: { type: "string" },
    api: { type: "string" },
    name: { type: "string" },
  });

  const webOrigin = (values.server || DEFAULT_SERVER).replace(/\/+$/, "");
  // --api 显式 > NEODROP_API env > 启发式推断
  const apiOrigin = values.api || ENV_API_OVERRIDE || inferApiOrigin(webOrigin);
  const clientName = values.name || detectClientName();

  note(`web   = ${webOrigin}`);
  note(`api   = ${apiOrigin}`);

  // 1. 起 session
  const session = await trpcMutation({ apiOrigin, token: null }, "cliToken.startSession", {
    clientName,
    webOrigin,
  });
  const sessionId = session.sessionId;
  // pollSecret 是后端只下发给本 CLI 进程的私有领取凭据，不进 verification URL；
  // poll 时必须回传它才能领到 token。URL 里只有 sessionId，截图/转发泄漏也领不走 token。
  const pollSecret = session.pollSecret;
  const verificationUrl = session.verificationUrl;
  const pollInterval = Math.max(1, Number(session.pollIntervalSeconds || 2));

  note("");
  note("👉 在任意浏览器（手机 / 笔记本 / 同机都行）打开下面 URL 完成授权：");
  note("");
  // URL 顶格单独成行（不加缩进）：这条 URL 含 ?session= 256bit 串、必然超 80 列，
  // 终端会折行。顶格单独一行最便于「三击选整行 / 鼠标拖整行」一次性把完整 URL 复制走。
  note(verificationUrl);
  note("");
  note("   （URL 较长会折行，复制时连同结尾的 ?session=... 一起选全）");
  note(`   客户端名「${clientName}」（在授权页确认是本次启动的 CLI）`);
  note("   授权链接 10 分钟内有效。授权后回到这个终端继续——CLI 会自动检测。");
  note("");

  // 2. 轮询
  const deadline = Date.now() + 10 * 60 * 1000; // 与后端 session 寿命对齐
  let waitedDots = 0;
  let token = null;
  let tokenId = null;
  let expiresAt = null;
  while (Date.now() < deadline) {
    await sleep(pollInterval * 1000);
    let res;
    try {
      res = await trpcQuery({ apiOrigin, token: null }, "cliToken.pollSession", {
        sessionId,
        pollSecret,
      });
    } catch (err) {
      // NOT_FOUND 一般是 session 已被后端清理；其它错误也直接抛
      throw new Error(`轮询授权失败：${err.message}`);
    }

    const status = res.status;
    if (status === "APPROVED") {
      if (res.alreadyClaimed) {
        throw new Error(
          "授权 token 已被领走（极少触发，正常应是本 CLI 自己领）。请重新 `npx neodrop login`。",
        );
      }
      token = res.token;
      tokenId = res.tokenId;
      const ea = res.tokenExpiresAt;
      expiresAt = typeof ea === "string" ? ea : ea ? String(ea) : null;
      if (!token) throw new Error("授权返回 APPROVED 但缺 token；请重新 `npx neodrop login`");
      break;
    }
    if (status === "DENIED") {
      throw new Error("授权被用户拒绝。如有需要请重新 `npx neodrop login` 并核对客户端名。");
    }
    if (status === "EXPIRED") {
      throw new Error("授权链接已过期（10 分钟内未授权）。请重新 `npx neodrop login`。");
    }
    // 还在 PENDING — 打点进度（每 5 次 poll 一个点，不刷屏）
    waitedDots += 1;
    if (waitedDots % 5 === 0) note(".", "");
  }

  if (!token) throw new Error("等待授权超时。请重新 `npx neodrop login`。");

  note("");

  // 3. 写凭证
  writeCredentials({
    webOrigin,
    apiOrigin,
    token,
    tokenId: tokenId || "",
    name: clientName,
    expiresAt: expiresAt || "",
    createdAt: new Date().toISOString(),
  });

  // 4. 校验 + 输出
  const me = await trpcQuery({ apiOrigin, token }, "user.getMe");
  note(`✅ 登录成功：${me.email || me.id || "<unknown>"}`);
  note(`   credentials = ${credentialsPath()}`);
  emit({
    ok: true,
    webOrigin,
    apiOrigin,
    user: me,
    tokenId,
    tokenName: clientName,
    expiresAt,
  });
}

async function cmdLogout() {
  const creds = readCredentials();
  if (creds === null) {
    note("未登录，无需登出。");
    emit({ ok: true, alreadyLoggedOut: true });
    return;
  }
  let revoked = false;
  try {
    await trpcMutation({ apiOrigin: creds.apiOrigin, token: creds.token }, "cliToken.revoke", {
      id: creds.tokenId,
    });
    revoked = true;
    note(`✅ 已撤销 ${creds.tokenId}`);
  } catch (err) {
    note(`⚠ 撤销失败（继续清本地凭证）：${err.message}`);
  }
  clearCredentials();
  emit({ ok: true, revoked });
}

async function cmdWhoami() {
  const { apiOrigin, token, creds } = authedCtx();
  const me = await trpcQuery({ apiOrigin, token }, "user.getMe");
  emit({
    webOrigin: creds.webOrigin,
    apiOrigin,
    tokenName: creds.name,
    tokenId: creds.tokenId,
    expiresAt: creds.expiresAt,
    user: me,
  });
}

async function cmdMe() {
  const { apiOrigin, token, creds } = authedCtx();
  const me = await trpcQuery({ apiOrigin, token }, "user.getMe");
  emit(me);
  if (me && typeof me === "object" && me.id) {
    note(`🔗 ${userUrl(creds.webOrigin, me.id)}`);
  }
}

async function cmdTokensList() {
  const { apiOrigin, token } = authedCtx();
  emit(await trpcQuery({ apiOrigin, token }, "cliToken.list"));
}

async function cmdTokensRevoke(argv) {
  const { positionals } = parse(argv, {});
  const id = requirePositional(positionals, 0, "用法：neodrop tokens revoke <id>");
  const { apiOrigin, token, creds } = authedCtx();
  const r = await trpcMutation({ apiOrigin, token }, "cliToken.revoke", { id });
  if (id === creds.tokenId) {
    clearCredentials();
    note("（撤销的是本机当前 token，已清除本地凭证。需要重新 neodrop login。）");
  }
  emit(r);
}

// ---- 频道命令 ---------------------------------------------------------

async function cmdChannelsList(argv) {
  const { values } = parse(argv, {
    mine: { type: "boolean" },
    limit: { type: "string" },
    cursor: { type: "string" },
    locale: { type: "string", default: "en" }, // 缺省 en，与 Web 默认 locale 一致
  });
  const { apiOrigin, token } = authedCtx();
  if (values.mine) {
    emit(await trpcQuery({ apiOrigin, token }, "channel.getMyChannels"));
    return;
  }
  const payload = { limit: toLimit(values.limit) ?? 20, locale: values.locale };
  if (values.cursor) payload.cursor = values.cursor;
  emit(await trpcQuery({ apiOrigin, token }, "channel.list", payload));
}

async function cmdChannelsGet(argv) {
  const { positionals } = parse(argv, {});
  const id = requirePositional(positionals, 0, "用法：neodrop channels get <channelId>");
  const { apiOrigin, token, creds } = authedCtx();
  emit(await trpcQuery({ apiOrigin, token }, "channel.getById", { id }));
  note(`🔗 ${channelUrl(creds.webOrigin, id)}`);
}

async function cmdChannelsCreate(argv) {
  const { values } = parse(argv, {
    name: { type: "string" },
    description: { type: "string" },
    type: { type: "string" },
    locale: { type: "string" },
    json: { type: "string" },
    stdin: { type: "boolean" },
  });
  if (values.json && values.stdin) {
    throw new UsageError("--json 与 --stdin 互斥，只能给一个");
  }
  if (values.type && values.type !== "PUBLIC" && values.type !== "PRIVATE") {
    throw new UsageError("--type 只能是 PUBLIC 或 PRIVATE");
  }
  const { apiOrigin, token } = authedCtx();
  let input = await loadInput(values);
  if (input === undefined) {
    if (!values.name) {
      throw new UsageError(
        "用法：neodrop channels create --name <X> [--description <Y>] [--type PUBLIC|PRIVATE] [--locale zh-cn]\n" +
          "或：neodrop channels create --json '{\"name\":\"X\",\"locale\":\"zh-cn\"}'\n" +
          "或：neodrop channels create --stdin",
      );
    }
    input = { name: values.name };
    if (values.description) input.description = values.description;
    if (values.type) input.type = values.type;
    if (values.locale) input.locale = values.locale;
  }
  emit(await trpcMutation({ apiOrigin, token }, "channel.create", input));
}

async function cmdChannelsSubscribe(argv) {
  const { positionals } = parse(argv, {});
  const id = requirePositional(positionals, 0, "用法：neodrop channels subscribe <channelId>");
  const { apiOrigin, token } = authedCtx();
  emit(await trpcMutation({ apiOrigin, token }, "channel.subscribe", { channelId: id }));
}

async function cmdChannelsUnsubscribe(argv) {
  const { positionals } = parse(argv, {});
  const id = requirePositional(positionals, 0, "用法：neodrop channels unsubscribe <channelId>");
  const { apiOrigin, token } = authedCtx();
  emit(await trpcMutation({ apiOrigin, token }, "channel.unsubscribe", { channelId: id }));
}

async function cmdChannelsSearch(argv) {
  const { values, positionals } = parse(argv, {
    limit: { type: "string" },
    locale: { type: "string" },
    strict: { type: "boolean" },
  });
  const query = requirePositional(positionals, 0, '用法：neodrop channels search "<query>"');
  const { apiOrigin, token } = authedCtx();
  const payload = { query };
  const limit = toLimit(values.limit);
  if (limit !== undefined) payload.limit = limit;
  if (values.locale) payload.locale = values.locale;
  if (values.strict) payload.strictLocale = true;
  emit(await trpcQuery({ apiOrigin, token }, "channel.searchPublic", payload));
}

async function cmdChannelsCategories() {
  const { apiOrigin, token } = authedCtx();
  emit(await trpcQuery({ apiOrigin, token }, "channel.getCategories"));
}

async function cmdChannelsByCategory(argv) {
  const { values, positionals } = parse(argv, {
    limit: { type: "string" },
    cursor: { type: "string" },
    locale: { type: "string" },
    sort: { type: "string" },
  });
  const slug = requirePositional(positionals, 0, "用法：neodrop channels by-category <slug>");
  if (values.sort && values.sort !== "latest" && values.sort !== "popular") {
    throw new UsageError("--sort 只能是 latest 或 popular");
  }
  const { apiOrigin, token } = authedCtx();
  const payload = { categorySlug: slug };
  const limit = toLimit(values.limit);
  if (limit !== undefined) payload.limit = limit;
  if (values.cursor) payload.cursor = values.cursor;
  if (values.locale) payload.locale = values.locale;
  if (values.sort) payload.sortBy = values.sort;
  emit(await trpcQuery({ apiOrigin, token }, "channel.listByCategory", payload));
}

// ---- post 命令 --------------------------------------------------------
// 命令面向用户的术语统一为 post；tRPC procedure 名仍是后端契约 `grain.*`，不动。

async function cmdPostsList(argv) {
  const { values } = parse(argv, {
    channel: { type: "string" },
    subscribed: { type: "boolean" },
    limit: { type: "string" },
    cursor: { type: "string" },
    locale: { type: "string" },
  });
  const { apiOrigin, token } = authedCtx();
  const limit = toLimit(values.limit) ?? 20;
  if (values.subscribed) {
    const payload = { limit };
    if (values.cursor) payload.cursor = values.cursor;
    if (values.channel) payload.channelId = values.channel;
    emit(await trpcQuery({ apiOrigin, token }, "grain.listSubscribed", payload));
    return;
  }
  if (values.channel) {
    const payload = { channelId: values.channel, limit };
    if (values.cursor) payload.cursor = values.cursor;
    emit(await trpcQuery({ apiOrigin, token }, "grain.list", payload));
    return;
  }
  // 否则 listRecent（公开 feed）
  const payload = { limit };
  if (values.cursor) payload.cursor = values.cursor;
  if (values.locale) payload.locale = values.locale;
  emit(await trpcQuery({ apiOrigin, token }, "grain.listRecent", payload));
}

async function cmdPostsGet(argv) {
  const { positionals } = parse(argv, {});
  const id = requirePositional(positionals, 0, "用法：neodrop posts get <postId>");
  const { apiOrigin, token, creds } = authedCtx();
  emit(await trpcQuery({ apiOrigin, token }, "grain.getById", { id }));
  note(`🔗 ${postUrl(creds.webOrigin, id)}`);
}

async function cmdPostsSearch(argv) {
  const { values, positionals } = parse(argv, {
    limit: { type: "string" },
    locale: { type: "string" },
    strict: { type: "boolean" },
  });
  const query = requirePositional(positionals, 0, '用法：neodrop posts search "<query>"');
  const { apiOrigin, token } = authedCtx();
  const payload = { query };
  const limit = toLimit(values.limit);
  if (limit !== undefined) payload.limit = limit;
  if (values.locale) payload.locale = values.locale;
  if (values.strict) payload.strictLocale = true;
  emit(await trpcQuery({ apiOrigin, token }, "grain.searchPublic", payload));
}

async function cmdFeed(argv) {
  const { values } = parse(argv, {
    limit: { type: "string" },
    cursor: { type: "string" },
  });
  const { apiOrigin, token } = authedCtx();
  const payload = { limit: toLimit(values.limit) ?? 20 };
  if (values.cursor) payload.cursor = values.cursor;
  emit(await trpcQuery({ apiOrigin, token }, "grain.listSubscribed", payload));
}

// ---- 兜底通道 ---------------------------------------------------------

async function cmdApi(argv) {
  const { values, positionals } = parse(argv, {
    json: { type: "string" },
    stdin: { type: "boolean" },
    mutation: { type: "boolean" },
  });
  if (values.json && values.stdin) {
    throw new UsageError("--json 与 --stdin 互斥，只能给一个");
  }
  const procedure = requirePositional(positionals, 0, "用法：neodrop api <procedure> [--json '...' | --stdin] [--mutation]");
  const { apiOrigin, token } = authedCtx();
  const input = await loadInput(values);
  if (values.mutation) {
    emit(await trpcMutation({ apiOrigin, token }, procedure, input));
  } else {
    emit(await trpcQuery({ apiOrigin, token }, procedure, input));
  }
}

// ---- skill 安装 -------------------------------------------------------

async function cmdInstallSkill(argv) {
  const { values } = parse(argv, {
    dest: { type: "string" },
  });
  const { target, copied } = installSkill({ dest: values.dest });
  note(`✅ 已安装 skill 到 ${target}`);
  note(`   拷入：${copied.join("、")}`);
  note("   重启 Claude Code（或新开会话）后，AI 看到 Neodrop 相关提问会自动调本 skill。");
  emit({ ok: true, target, copied });
}

// ---- 帮助 -------------------------------------------------------------

const HELP = `neodrop — Neodrop CLI（AI agent 与人类共用，stdout = JSON）

用法：
  npx neodrop <command> [args...]

元命令：
  login [--server <url>] [--api <url>] [--name <名>]   授权登录，写 PAT 到 ~/.neodrop/credentials.json
  logout                                               撤销 PAT + 删本地凭证
  whoami                                               当前 token + user 信息
  me                                                   当前用户信息（user.getMe）
  tokens list                                          列出所有 PAT
  tokens revoke <id>                                   撤销指定 PAT
  install-skill [--dest <dir>]                         把 SKILL.md + references 装进 agent skill 目录
                                                       （默认 ${defaultSkillDest()}）

频道：
  channels list [--mine] [--limit N] [--cursor C] [--locale L]
  channels get <channelId>
  channels create --name <X> [--description <Y>] [--type PUBLIC|PRIVATE] [--locale L]
  channels create --json '{...}' | --stdin
  channels subscribe <channelId>
  channels unsubscribe <channelId>
  channels search "<query>" [--limit N] [--locale L] [--strict]
  channels categories
  channels by-category <slug> [--limit N] [--cursor C] [--locale L] [--sort latest|popular]

内容（Post）：
  posts list [--subscribed | --channel <id>] [--limit N] [--cursor C] [--locale L]
  posts get <postId>
  posts search "<query>" [--limit N] [--locale L] [--strict]
  feed [--limit N] [--cursor C]                        = posts list --subscribed

兜底：
  api <procedure> [--json '...' | --stdin] [--mutation]

全局：
  --pretty                                             缩进 JSON 输出（仍是合法 JSON）

环境变量：NEODROP_SERVER（web origin）/ NEODROP_API（api origin）
更多见 SKILL.md 与 references/。`;

// ---- 路由 -------------------------------------------------------------

const TOKENS_SUB = {
  list: cmdTokensList,
  revoke: cmdTokensRevoke,
};
const CHANNELS_SUB = {
  list: cmdChannelsList,
  get: cmdChannelsGet,
  create: cmdChannelsCreate,
  subscribe: cmdChannelsSubscribe,
  unsubscribe: cmdChannelsUnsubscribe,
  search: cmdChannelsSearch,
  categories: cmdChannelsCategories,
  "by-category": cmdChannelsByCategory,
};
const POSTS_SUB = {
  list: cmdPostsList,
  get: cmdPostsGet,
  search: cmdPostsSearch,
};

async function dispatchGroup(name, table, argv) {
  const sub = argv[0];
  if (!sub || sub === "--help" || sub === "-h") {
    throw new UsageError(`用法：neodrop ${name} <${Object.keys(table).join(" | ")}>`);
  }
  const handler = table[sub];
  if (!handler) {
    throw new UsageError(`未知子命令 ${name} ${sub}（可用：${Object.keys(table).join(" / ")}）`);
  }
  await handler(argv.slice(1));
}

async function dispatch(rawArgs) {
  // 全局 --pretty 可放任意位置：先剥出来，剩下的交给各命令解析。
  const pretty = rawArgs.includes("--pretty");
  setPretty(pretty);
  const args = rawArgs.filter((a) => a !== "--pretty");

  const cmd = args[0];
  if (!cmd || cmd === "--help" || cmd === "-h" || cmd === "help") {
    note(HELP);
    return;
  }

  const rest = args.slice(1);
  switch (cmd) {
    case "login":
      return cmdLogin(rest);
    case "logout":
      return cmdLogout();
    case "whoami":
      return cmdWhoami();
    case "me":
      return cmdMe();
    case "tokens":
      return dispatchGroup("tokens", TOKENS_SUB, rest);
    case "channels":
      return dispatchGroup("channels", CHANNELS_SUB, rest);
    case "grains": // 向后兼容：旧命令名，已更名为 posts
    case "posts":
      return dispatchGroup("posts", POSTS_SUB, rest);
    case "feed":
      return cmdFeed(rest);
    case "api":
      return cmdApi(rest);
    case "install-skill":
      return cmdInstallSkill(rest);
    default:
      throw new UsageError(`未知命令「${cmd}」。运行 neodrop --help 看全部命令。`);
  }
}

async function main() {
  try {
    await dispatch(process.argv.slice(2));
  } catch (err) {
    if (err instanceof UsageError) {
      note(`✗ ${err.message}`);
      process.exitCode = 2;
      return;
    }
    // ApiError（tRPC 业务错）与其它运行时错误统一退出码 1
    note(`✗ ${err.message}`);
    process.exitCode = 1;
  }
}

await main();
