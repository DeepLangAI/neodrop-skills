// 本地凭证（~/.neodrop/credentials.json，chmod 0600）。
//
// 只支持单一 active token——切换 server / 换号都走 logout → login。
//
// 凭证 schema：
//   webOrigin  string  产品域（neodrop.ai；本地 dev 4001）
//   apiOrigin  string  API 域（api.neodrop.ai；本地 dev 3001）—— 与 web 拆开是因为部署不同域
//   token      string  PAT 明文，仅本机；权限 0600
//   tokenId    string  用于 logout / 远程撤销
//   name       string  在 /settings/cli-tokens 上展示的客户端名
//   expiresAt  string  ISO 8601，默认 90 天
//   createdAt  string  ISO 8601

import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const FILE_DIR = join(homedir(), ".neodrop");
const FILE_PATH = join(FILE_DIR, "credentials.json");

export function credentialsPath() {
  return FILE_PATH;
}

export function readCredentials() {
  if (!existsSync(FILE_PATH)) return null;
  try {
    return JSON.parse(readFileSync(FILE_PATH, "utf-8"));
  } catch (err) {
    throw new Error(`无法解析 ${FILE_PATH}：${err.message}`);
  }
}

// 原子写：写临时文件 → chmod 0600 → rename。chmod 在 rename 之前完成，确保最终
// 文件出现的瞬间权限就是 0600，不存在其他进程能读到 world-readable 的窗口。
export function writeCredentials(creds) {
  mkdirSync(FILE_DIR, { recursive: true });
  const tmp = `${FILE_PATH}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(creds, null, 2)}\n`, { encoding: "utf-8", mode: 0o600 });
  chmodSync(tmp, 0o600);
  renameSync(tmp, FILE_PATH);
}

export function clearCredentials() {
  try {
    unlinkSync(FILE_PATH);
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }
}

export function requireCredentials() {
  const creds = readCredentials();
  if (creds === null) {
    throw new Error("未登录。先运行：npx neodrop login");
  }
  return creds;
}
