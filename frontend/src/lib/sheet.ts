/**
 * Excel 格子读写（SheetJS）。无公式引擎、无数据透视。给工作台表格查看器用。
 */
import * as XLSX from "xlsx";

export type SheetBook = {
  names: string[];
  sheets: Record<string, string[][]>;
};

export function parseWorkbook(buf: ArrayBuffer): SheetBook {
  /** 【功能】ArrayBuffer → {names, sheets} 二维格子。 */
  if (!buf.byteLength) {
    return { names: ["Sheet1"], sheets: { Sheet1: emptyGrid() } };
  }
  try {
    const wb = XLSX.read(buf, { type: "array" });
    const names = wb.SheetNames.length ? wb.SheetNames : ["Sheet1"];
    const sheets: Record<string, string[][]> = {};
    for (const name of names) {
      const raw = XLSX.utils.sheet_to_json<(string | number | boolean | null)[]>(wb.Sheets[name], {
        header: 1,
        defval: "",
        raw: false,
      });
      const grid = (raw.length ? raw : [["", ""]]).map((row) =>
        (row.length ? row : [""]).map((cell) => String(cell ?? "")),
      );
      sheets[name] = padGrid(grid);
    }
    return { names, sheets };
  } catch {
    return { names: ["Sheet1"], sheets: { Sheet1: emptyGrid() } };
  }
}

export function emptyGrid(rows = 16, cols = 8): string[][] {
  return Array.from({ length: rows }, () => Array.from({ length: cols }, () => ""));
}

function padGrid(grid: string[][], minRows = 8, minCols = 6): string[][] {
  const cols = Math.max(minCols, ...grid.map((r) => r.length), 1);
  const rows = grid.map((r) => {
    const next = [...r];
    while (next.length < cols) next.push("");
    return next;
  });
  while (rows.length < minRows) rows.push(Array.from({ length: cols }, () => ""));
  return rows;
}

export function writeWorkbook(book: SheetBook, bookType: "xlsx" | "xls" = "xlsx"): Uint8Array {
  const wb = XLSX.utils.book_new();
  const names = book.names.length ? book.names : ["Sheet1"];
  for (const name of names) {
    const ws = XLSX.utils.aoa_to_sheet(book.sheets[name] || [[""]]);
    XLSX.utils.book_append_sheet(wb, ws, name.slice(0, 31) || "Sheet");
  }
  const raw = XLSX.write(wb, { bookType, type: "array" });
  if (raw instanceof ArrayBuffer) return new Uint8Array(raw);
  if (raw instanceof Uint8Array) return raw;
  return Uint8Array.from(raw as number[]);
}

export function colLabel(index: number): string {
  let n = index + 1;
  let label = "";
  while (n > 0) {
    n -= 1;
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26);
  }
  return label;
}

export function parseCsv(text: string, tab = false): string[][] {
  const sep = tab ? "\t" : ",";
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
  const rows = lines.map((line) => line.split(sep));
  return padGrid(rows.length ? rows : [[""]]);
}

export function stringifyCsv(rows: string[][], tab = false): string {
  const sep = tab ? "\t" : ",";
  return rows
    .map((row) =>
      row
        .map((cell) => {
          if (/["\n,]/.test(cell) || (tab && cell.includes("\t"))) {
            return `"${cell.replace(/"/g, '""')}"`;
          }
          return cell;
        })
        .join(sep),
    )
    .join("\n");
}
