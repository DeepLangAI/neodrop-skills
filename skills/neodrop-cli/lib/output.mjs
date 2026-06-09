// 统一输出：stdout = JSON（AI 直接 JSON.parse），stderr = 日志/提示。
//
// 默认单行 JSON；--pretty 切 2 空格缩进 JSON——两者都是合法 JSON，AI 不需要
// flag 切换也能解析。

let pretty = false;

export function setPretty(value) {
  pretty = Boolean(value);
}

export function emit(data) {
  if (data === null || data === undefined) return;
  process.stdout.write(`${JSON.stringify(data, null, pretty ? 2 : undefined)}\n`);
}

export function note(msg, end = "\n") {
  process.stderr.write(msg + end);
}
