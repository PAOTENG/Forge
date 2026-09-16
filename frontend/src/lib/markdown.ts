/**
 * Markdown → 消毒后的 HTML。工作台预览 md，不给模型用。
 */
import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

export function renderMarkdown(source: string): string {
  /** 【功能】GFM 渲染并 DOMPurify。输入 md 源，输出安全 HTML。 */
  const html = marked.parse(source, { async: false }) as string;
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}
