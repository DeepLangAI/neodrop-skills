// 把 SKILL.md + references/ 拷进 agent 的 skill 目录，让 `npx neodrop` 一条命令
// 既装好 CLI 又装好 skill 描述。
//
// npm/npx 只分发「可执行文件」；而 Claude Code 等 agent 的 skill 是 SKILL.md +
// references/ 这组文件落进 agent 的 skill 目录后才会被路由。两条分发渠道本来是
// 分开的，这个命令把它们合并成一步。
//
// 默认目标 ~/.claude/skills/neodrop-cli/（Claude Code 约定）；--dest 可改到别的
// agent 的 skill 目录。目录名固定 neodrop-cli，与 SKILL.md frontmatter 的 name 一致。

import { cpSync, existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export function defaultSkillDest() {
  return join(homedir(), ".claude", "skills", "neodrop-cli");
}

export function installSkill({ dest } = {}) {
  // 包根目录 = 本文件所在 lib/ 的上一层（bin/ 与 lib/ 与 SKILL.md 同级）
  const here = dirname(fileURLToPath(import.meta.url)); // .../lib
  const pkgRoot = dirname(here);
  const target = dest || defaultSkillDest();

  const skillSrc = join(pkgRoot, "SKILL.md");
  if (!existsSync(skillSrc)) {
    throw new Error(`找不到 SKILL.md（期望在 ${skillSrc}）；npm 包可能未按 files 白名单打进 SKILL.md`);
  }

  mkdirSync(target, { recursive: true });
  cpSync(skillSrc, join(target, "SKILL.md"));

  const refSrc = join(pkgRoot, "references");
  const copied = ["SKILL.md"];
  if (existsSync(refSrc)) {
    cpSync(refSrc, join(target, "references"), { recursive: true });
    copied.push("references/");
  }

  return { target, copied };
}
