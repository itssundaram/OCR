import React, { useMemo, useState } from 'react';
import { Table as TableIcon, Download, Copy, Check, FileJson, AlertTriangle, Pencil } from 'lucide-react';
import { extractService } from '../services/extract';
import {
  buildGrid,
  countLowConfidence,
  downloadText,
  gridToCsv,
  gridToRecords,
  gridToTsv,
  isLowConfidence,
} from '../utils/tableGrid';

/**
 * Editable grid for one extracted table.
 *
 *  - row numbers on the left, header row pinned on top
 *  - cells the reader was unsure about are tinted (only when it gave a score)
 *  - click a cell to edit; Enter saves it back to the job result, Esc cancels
 *  - per-table CSV / JSON download and copy-as-TSV (pastes into Excel)
 */
const TableGrid = ({ table, tableNumber, jobId, onTableUpdated, onToast }) => {
  const { grid, nRows, nCols } = useMemo(() => buildGrid(table), [table]);
  const lowCount = useMemo(() => countLowConfidence(grid), [grid]);
  const [editing, setEditing] = useState(null); // { row, col }
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  const baseName = `table_${tableNumber}`;
  const method = table.extraction_method;
  const pageNo = Number(table.page_number) || 1;
  const bodyRows = Math.max(0, nRows - 1);

  const startEdit = (r, c) => {
    if (saving) return;
    setEditing({ row: r, col: c });
    setDraft(grid[r][c].text);
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft('');
  };

  const commitEdit = async () => {
    if (!editing || saving) return;
    const { row, col } = editing;
    if (draft === grid[row][col].text) {
      cancelEdit();
      return;
    }
    setSaving(true);
    try {
      const res = await extractService.updateTableCell(jobId, table.table_index, row, col, draft);
      if (res?.data?.table) onTableUpdated(table.table_index, res.data.table);
      onToast?.(`Row ${row}, column ${col + 1} saved`, 'success');
      cancelEdit();
    } catch (err) {
      onToast?.(`Could not save cell: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(gridToTsv(grid));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      onToast?.('Copy is not available in this browser', 'error');
    }
  };

  if (nRows === 0 || nCols === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl p-4 text-xs text-slate-500">
        Table {tableNumber} was detected on page {pageNo} but no cells could be read from it.
      </div>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
      {/* Title bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 bg-slate-50 border-b border-slate-200">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <TableIcon size={15} className="text-blue-600 flex-shrink-0" />
          <span className="text-xs font-bold text-slate-800">Table {tableNumber}</span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
            Page {pageNo}
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
            {bodyRows} {bodyRows === 1 ? 'row' : 'rows'} × {nCols} cols
          </span>
          {method && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200">
              {method}
            </span>
          )}
          {lowCount > 0 && (
            <span className="inline-flex items-center gap-1 text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-300">
              <AlertTriangle size={10} /> {lowCount} to review
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={handleCopy}
            className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer"
            title="Copy as tab-separated text (paste into Excel or Sheets)"
          >
            {copied ? <Check size={11} className="text-emerald-600" /> : <Copy size={11} />} Copy
          </button>
          <button
            onClick={() => downloadText(`${baseName}.csv`, gridToCsv(grid), 'text/csv')}
            className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer"
            title="Download this table as CSV"
          >
            <Download size={11} /> CSV
          </button>
          <button
            onClick={() =>
              downloadText(`${baseName}.json`, JSON.stringify(gridToRecords(grid), null, 2), 'application/json')
            }
            className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer"
            title="Download this table as JSON (one object per row)"
          >
            <FileJson size={11} /> JSON
          </button>
        </div>
      </div>

      {/* Grid */}
      <div className="overflow-auto max-h-[60vh]">
        <table className="border-collapse text-xs w-max min-w-full">
          <thead>
            <tr>
              <th className="sticky top-0 left-0 z-20 w-10 min-w-[2.5rem] bg-slate-100 border-b border-r border-slate-200 text-[10px] font-semibold text-slate-400 px-2 py-2 text-center">
                #
              </th>
              {grid[0].map((cell, c) => (
                <th
                  key={c}
                  className="sticky top-0 z-10 bg-slate-100 border-b border-r border-slate-200 last:border-r-0 p-0 text-left align-top"
                >
                  <GridCell
                    cell={cell}
                    isHeader
                    isEditing={editing?.row === 0 && editing?.col === c}
                    draft={draft}
                    setDraft={setDraft}
                    onStart={() => startEdit(0, c)}
                    onCommit={commitEdit}
                    onCancel={cancelEdit}
                    saving={saving}
                    placeholder={`Column ${c + 1}`}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.slice(1).map((row, i) => {
              const r = i + 1;
              return (
                <tr key={r} className="group/row hover:bg-blue-50/30">
                  <td className="sticky left-0 z-10 bg-slate-50 group-hover/row:bg-blue-50 border-b border-r border-slate-200 text-[10px] font-mono text-slate-400 text-center px-2">
                    {r}
                  </td>
                  {row.map((cell, c) => (
                    <td key={c} className="border-b border-r border-slate-100 last:border-r-0 p-0 align-top">
                      <GridCell
                        cell={cell}
                        isEditing={editing?.row === r && editing?.col === c}
                        draft={draft}
                        setDraft={setDraft}
                        onStart={() => startEdit(r, c)}
                        onCommit={commitEdit}
                        onCancel={cancelEdit}
                        saving={saving}
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
            {bodyRows === 0 && (
              <tr>
                <td colSpan={nCols + 1} className="px-4 py-3 text-center text-[11px] text-slate-400 italic">
                  Only a header row was read for this table.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="px-4 py-1.5 bg-slate-50 border-t border-slate-200 text-[10px] text-slate-400 flex items-center gap-1.5">
        <Pencil size={10} /> Click any cell to correct it. Corrected cells are marked and saved to this result.
      </div>
    </div>
  );
};

const GridCell = ({ cell, isHeader = false, isEditing, draft, setDraft, onStart, onCommit, onCancel, saving, placeholder }) => {
  if (isEditing) {
    return (
      <input
        autoFocus
        value={draft}
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onCommit();
          if (e.key === 'Escape') onCancel();
        }}
        onBlur={onCancel}
        className="w-full min-w-[8rem] px-2.5 py-2 text-xs font-mono bg-white outline-none ring-2 ring-inset ring-blue-500 text-slate-900"
      />
    );
  }

  const low = !isHeader && isLowConfidence(cell);
  const tone = low
    ? 'bg-amber-50 text-amber-900'
    : cell.edited
    ? 'bg-emerald-50/70 text-slate-900'
    : isHeader
    ? 'text-slate-800 font-bold'
    : 'text-slate-700';

  const title = low
    ? `Low confidence (${Math.round(cell.confidence * 100)}%). Click to correct.`
    : cell.edited
    ? 'Corrected by a reviewer'
    : 'Click to edit';

  return (
    <div
      onClick={onStart}
      title={title}
      className={`relative px-2.5 py-2 min-w-[6rem] min-h-[2.1rem] font-mono cursor-text whitespace-pre-wrap break-words hover:bg-blue-50 ${tone}`}
    >
      {cell.text !== '' ? (
        cell.text
      ) : (
        <span className="text-slate-300">{isHeader ? placeholder : ''}</span>
      )}
      {low && <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-amber-500" />}
      {cell.edited && !low && <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-emerald-500" />}
    </div>
  );
};

export default TableGrid;
