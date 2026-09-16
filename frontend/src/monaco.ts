/**
 * Monaco 工人注册：按语言选 json/css/html/ts worker。工作台打开代码文件时用。
 */
import editorWorker from "monaco-editor/editor/editor.worker.js?worker";
import jsonWorker from "monaco-editor/language/json/json.worker.js?worker";
import cssWorker from "monaco-editor/language/css/css.worker.js?worker";
import htmlWorker from "monaco-editor/language/html/html.worker.js?worker";
import tsWorker from "monaco-editor/language/typescript/ts.worker.js?worker";
import * as monaco from "monaco-editor";

const g = globalThis as typeof globalThis & {
  MonacoEnvironment?: { getWorker: (_id: string, label: string) => Worker };
};
g.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new cssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};

const EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  vue: "html",
  py: "python",
  json: "json",
  md: "markdown",
  markdown: "markdown",
  tex: "plaintext",
  ltx: "plaintext",
  latex: "plaintext",
  csv: "plaintext",
  css: "css",
  html: "html",
  yml: "yaml",
  yaml: "yaml",
  toml: "ini",
  sh: "shell",
  sql: "sql",
  xml: "xml",
  ini: "ini",
  env: "ini",
  log: "plaintext",
  conf: "ini",
  rs: "rust",
  go: "go",
  java: "java",
  kt: "kotlin",
  rb: "ruby",
  php: "php",
  cs: "csharp",
  jsonc: "json",
  tsv: "plaintext",
  txt: "plaintext",
};

export function languageFor(path: string) {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXT[ext] ?? "plaintext";
}

export { monaco };
