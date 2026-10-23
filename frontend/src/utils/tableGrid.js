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
      Array.from({ length: nCols }, () => ({ text: '', confidence: null, edited: false, state: null, reasons: [], suggestion: null, rawText: null, rev: 0, bbox: null, page: null, ocrConfidence: null }))
    );
    cells.forEach((c) => {
      const r = Number(c.row ?? 0);
      const col = Number(c.col ?? 0);
      grid[r][col] = {
        text: c.text == null ? '' : String(c.text),
        confidence: typeof c.confidence === 'number' ? c.confidence : null,
        edited: Boolean(c.edited),
        state: c.state || null,                       // accepted | flagged | empty (absent on older results)
        reasons: Array.isArray(c.reasons) ? c.reasons : [],
        suggestion: c.suggestion || null,
        rawText: c.raw_text ?? null,
        rev: Number(c.rev || 0),
        bbox: c.bbox || null,
        page: c.page || null,
        ocrConfidence: typeof c.ocr_confidence === 'number' ? c.ocr_confidence : null,
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
  if (cell != null && cell.state) return cell.state === 'flagged' && !cell.edited;   // the server's verdict wins
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

/**
 * Fold `cell_refined` events into stored tables. Safe to call again with the same
 * patches: a patch only applies when it is newer than the cell (rev) and the cell
 * was not corrected by a person.
 */
export function applyCellPatches(tables, patches) {
  if (!patches || patches.length === 0) return tables;
  const out = tables.map((t) => ({ ...t, cells: t.cells ? t.cells.map((c) => ({ ...c })) : t.cells, rows: t.rows ? t.rows.map((r) => [...r]) : t.rows, headers: t.headers ? [...t.headers] : t.headers }));
  patches.forEach((p) => {
    const t = out.find((x) => x.table_index === p.table_index);
    const cell = t?.cells?.find((c) => c.row === p.row && c.col === p.col);
    if (!cell || cell.edited || Number(cell.rev || 0) >= Number(p.rev || 0)) return;
    if (cell.raw_text == null && cell.text !== p.text) cell.raw_text = cell.text;
    cell.text = p.text;
    cell.state = p.state;
    cell.confidence = p.confidence;
    cell.reasons = p.reasons || [];
    cell.rev = p.rev;
    delete cell.suggestion;
    if (p.row === 0) t.headers[p.col] = p.text;
    else if (t.rows[p.row - 1]) t.rows[p.row - 1][p.col] = p.text;
  });
  return out;
}

/** Live summary of one table from its cells (also correct after edits and refinements). */
export function tableSummary(table, grid) {
  const g = grid || buildGrid(table).grid;
  let accepted = 0;
  let flagged = 0;
  let suggestions = 0;
  const perCol = [];
  g.forEach((row, r) => {
    if (r === 0) return;
    row.forEach((cell, c) => {
      if (cell.text === '') return;
      perCol[c] = perCol[c] || { flagged: 0, accepted: 0 };
      if (isLowConfidence(cell)) {
        flagged += 1;
        perCol[c].flagged += 1;
        if (cell.suggestion) suggestions += 1;
      } else {
        accepted += 1;
        perCol[c].accepted += 1;
      }
    });
  });
  const q = table?.meta?.quality || {};
  return {
    accepted,
    flagged,
    suggestions,
    perCol,
    countCheck: q.count_check || null,
    serial: q.serial || null,
    notes: q.notes || [],
    columns: table?.meta?.columns || [],
    pages: table?.meta?.pages || [table?.page_number].filter(Boolean),
    title: table?.meta?.title_text || '',
    footer: table?.meta?.footer_text || '',
  };
}

/** Row numbers (1-based body rows) holding at least one flagged cell. */
export function flaggedRows(grid) {
  const rows = [];
  grid.forEach((row, r) => {
    if (r > 0 && row.some((c) => isLowConfidence(c))) rows.push(r);
  });
  return rows;
}
