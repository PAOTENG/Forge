/**
 * Word 查看器辅助：docx → HTML（mammoth），保存时再打成 docx。
 * 复杂版式/修订/宏会简化。给 DocxEditor.vue 用，不是 Agent 工具。
 */
import DOMPurify from "dompurify";
import { Document, HeadingLevel, Packer, Paragraph, TextRun, UnderlineType } from "docx";
import mammoth from "mammoth";

export const EMPTY_DOC_HTML = "<p></p>";

type Marks = { bold?: boolean; italics?: boolean; underline?: boolean };

export async function docxToHtml(buf: ArrayBuffer): Promise<string> {
  /** 【功能】把 .docx 二进制转成可编辑 HTML。空文件返回空段落。 */
  if (!buf.byteLength) return EMPTY_DOC_HTML;
  try {
    const result = await mammoth.convertToHtml({ arrayBuffer: buf });
    const html = DOMPurify.sanitize(result.value || "", { USE_PROFILES: { html: true } });
    return html.trim() ? html : EMPTY_DOC_HTML;
  } catch {
    return EMPTY_DOC_HTML;
  }
}

export async function htmlToDocx(html: string): Promise<Uint8Array> {
  const parsed = new DOMParser().parseFromString(
    `<div id="root">${html || EMPTY_DOC_HTML}</div>`,
    "text/html",
  );
  const root = parsed.getElementById("root");
  const children = blocksFrom(root);
  const doc = new Document({
    sections: [
      {
        children: children.length ? children : [new Paragraph({ children: [new TextRun("")] })],
      },
    ],
  });
  const blob = await Packer.toBlob(doc);
  return new Uint8Array(await blob.arrayBuffer());
}

function blocksFrom(root: Element | null): Paragraph[] {
  if (!root) return [];
  const out: Paragraph[] = [];
  const nodes = [...root.childNodes];
  if (!nodes.length) return [new Paragraph({ children: [new TextRun("")] })];
  for (const node of nodes) {
    out.push(...nodeToBlocks(node));
  }
  return out.length ? out : [new Paragraph({ children: [new TextRun("")] })];
}

function nodeToBlocks(node: Node): Paragraph[] {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = (node.textContent || "").replace(/\s+/g, " ");
    if (!text.trim()) return [];
    return [new Paragraph({ children: [new TextRun(text)] })];
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return [];
  const el = node as HTMLElement;
  const tag = el.tagName.toLowerCase();
  if (tag === "br") {
    return [new Paragraph({ children: [new TextRun("")] })];
  }
  if (tag === "ul" || tag === "ol") {
    return [...el.children].flatMap((li) => {
      const runs = runsFrom(li, {});
      const prefix = tag === "ol" ? "" : "• ";
      const children = prefix
        ? [new TextRun(prefix), ...runs]
        : runs.length
          ? runs
          : [new TextRun("")];
      return [new Paragraph({ children })];
    });
  }
  const heading =
    tag === "h1"
      ? HeadingLevel.HEADING_1
      : tag === "h2"
        ? HeadingLevel.HEADING_2
        : tag === "h3"
          ? HeadingLevel.HEADING_3
          : undefined;
  if (heading || tag === "p" || tag === "div" || tag === "blockquote") {
    const runs = runsFrom(el, {});
    return [
      new Paragraph({
        heading,
        children: runs.length ? runs : [new TextRun("")],
      }),
    ];
  }
  if (["strong", "b", "em", "i", "u", "span", "a"].includes(tag)) {
    const runs = runsFrom(el, marksFrom(el, {}));
    return [new Paragraph({ children: runs.length ? runs : [new TextRun("")] })];
  }
  return [...el.childNodes].flatMap((child) => nodeToBlocks(child));
}

function marksFrom(el: HTMLElement, base: Marks): Marks {
  const tag = el.tagName.toLowerCase();
  return {
    bold: base.bold || tag === "strong" || tag === "b",
    italics: base.italics || tag === "em" || tag === "i",
    underline: base.underline || tag === "u",
  };
}

function runsFrom(node: Node, marks: Marks): TextRun[] {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.textContent || "";
    if (!text) return [];
    return [
      new TextRun({
        text,
        bold: marks.bold,
        italics: marks.italics,
        underline: marks.underline ? { type: UnderlineType.SINGLE } : undefined,
      }),
    ];
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return [];
  const el = node as HTMLElement;
  if (el.tagName.toLowerCase() === "br") {
    return [new TextRun({ break: 1 })];
  }
  const next = marksFrom(el, marks);
  return [...el.childNodes].flatMap((child) => runsFrom(child, next));
}
