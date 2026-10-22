/**
 * DOCINT — table grid helpers (pure functions, no React).
 *
 * A stored table looks like
 *   { table_index, page_number, headers, rows, cells:[{row,col,text,confidence,...}], markdown }
 * `cells` is the source of truth when present; readers without cell data
 * (older results) only have headers + rows, so we build the grid from those.
 */

/** Cells scoring below this are highlighted for review. */
export const LOW_CONFIDENCE = 0.7;

/**
 * Normalise a table into a rectangular grid.
 * Returns { grid, nRows, nCols } where grid[r][c] = { text, confidence, edited }.
 * Row 0 is the header row.
 */
export function buildGrid(table) {
  const cells = Array.isArray(table?.cells) ? table.cells : [];
  let grid = [];

  if (cells.length > 0) {
    const nRows = Math.max(...cells.map((c) => Number(c.row ?? 0))) + 1;
    const nCols = Math.max(...cells.map((c) => Number(c.col ?? 0))) + 1;
    grid = Array.from({ length: nRows }, () =>
      Array.from({ length: nCols }, () => ({ text: '', confidence: null, edited: false }))
    );
    cells.forEach((c) => {
      const r = Number(c.row ?? 0);
      const col = Number(c.col ?? 0);
      grid[r][col] = {
        text: c.text == null ? '' : String(c.text),
        confidence: typeof c.confidence === 'number' ? c.confidence : null,
        edited: Boolean(c.edited),
      };
    });
  } else {
    const all = [table?.headers || [], ...(table?.rows || [])];
    const nCols = Math.max(0, ...all.map((r) => r.length));
    grid = nCols === 0 ? [] : all.map((row) =>
      Array.from({ length: nCols }, (_, c) => ({
        text: row[c] == null ? '' : String(row[c]),
        confidence: null,
        edited: false,
      }))
    );
  }

  return { grid, nRows: grid.length, nCols: grid[0]?.length || 0 };
}

/** True when the cell has a score below the review threshold. */
export function isLowConfidence(cell, threshold = LOW_CONFIDENCE) {
  return cell != null && typeof cell.confidence === 'number' && cell.confidence < threshold && cell.text !== '';
}

/** How many non-empty cells of the grid need review. */
export function countLowConfidence(grid, threshold = LOW_CONFIDENCE) {
  let n = 0;
  grid.forEach((row, r) => {
    if (r === 0) return; // header row is not scored for review
    row.forEach((cell) => {
      if (isLowConfidence(cell, threshold)) n += 1;
    });
  });
  return n;
}

function csvEscape(text) {
  const s = text == null ? '' : String(text);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** RFC 4180 CSV for the whole grid (header row first). */
export function gridToCsv(grid) {
  return grid.map((row) => row.map((c) => csvEscape(c.text)).join(',')).join('\r\n');
}

/** Tab separated text, pastes straight into Excel / Sheets. */
export function gridToTsv(grid) {
  return grid
    .map((row) => row.map((c) => String(c.text).replace(/[\t\r\n]+/g, ' ')).join('\t'))
    .join('\n');
}

/**
 * JSON array of row objects keyed by the header row.
 * Blank headers become "column_N" and duplicates get a numeric suffix so no value is lost.
 */
export function gridToRecords(grid) {
  if (grid.length === 0) return [];
  const used = new Set();
  const keys = grid[0].map((cell, i) => {
    let base = (cell.text || '').trim() || `column_${i + 1}`;
    let key = base;
    let n = 2;
    while (used.has(key)) key = `${base}_${n++}`;
    used.add(key);
    return key;
  });
  return grid.slice(1).map((row) => {
    const rec = {};
    keys.forEach((k, i) => {
      rec[k] = row[i]?.text ?? '';
    });
    return rec;
  });
}

/** Save text as a file in the browser. */
export function downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
