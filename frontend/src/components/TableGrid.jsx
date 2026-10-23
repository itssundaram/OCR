import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Table as TableIcon, Download, Copy, Check, FileJson, AlertTriangle, Pencil, CheckCircle2, Loader2,
  ChevronUp, ChevronDown, Filter, Wand2, MapPin, ListChecks, X,
} from 'lucide-react';
import { extractService } from '../services/extract';
import {
  buildGrid,
  downloadText,
  flaggedRows,
  gridToCsv,
  gridToRecords,
  gridToTsv,
  isLowConfidence,
  tableSummary,
} from '../utils/tableGrid';

/**
 * Review grid for one extracted table.
 *
 *  - every cell carries a confidence bar and, when the server flagged it, the reasons why
 *  - the header shows each column's detected type and how many of its cells need a look
 *  - a summary line: accepted / flagged, and the document's own row count vs rows read
 *  - while the server keeps re-reading flagged cells, a banner says so and cells that
 *    change flash briefly (the server reads what you are looking at first)
 *  - click a cell: close-up of the cell, reasons, a one-click suggestion, "show on page"
 *  - double-click (or Enter) edits; a person's value is never replaced by a background re-read
 */
const KIND_LABEL = {
  serial: 'No.', date: 'Date', id: 'ID', enum: 'List', number: 'Number', name: 'Name', text: 'Text', nonlatin: 'Hindi / handwriting',
};

const confColor = (c, flagged) => {
  if (flagged) return c != null && c < 0.4 ? 'bg-red-400' : 'bg-amber-400';
  return 'bg-emerald-400';
};

const TableGrid = ({ table, tableNumber, jobId, onTableUpdated, onToast, refine, onLocateCell, onFocusRows }) => {
  const { grid, nRows, nCols } = useMemo(() => buildGrid(table), [table]);
  const summary = useMemo(() => tableSummary(table, grid), [table, grid]);
  const flaggedList = useMemo(() => flaggedRows(grid), [grid]);
  const [editing, setEditing] = useState(null); // { row, col }
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [selected, setSelected] = useState(null); // { row, col }
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [flash, setFlash] = useState({});
  const [confirmApply, setConfirmApply] = useState(false);
  const scrollRef = useRef(null);
  const rowRefs = useRef({});
  const prevRevs = useRef({});

  const baseName = `table_${tableNumber}`;
  const method = table.extraction_method;
  const pages = summary.pages.length ? summary.pages : [Number(table.page_number) || 1];
  const pageLabel = pages.length > 1 ? `Pages ${pages[0]}–${pages[pages.length - 1]}` : `Page ${pages[0]}`;
  const bodyRows = Math.max(0, nRows - 1);
  const refining = refine && (refine.status === 'running' || refine.status === 'pending');

  // cells changed by the background refiner flash for a moment
  useEffect(() => {
    const changed = {};
    grid.forEach((row, r) => row.forEach((cell, c) => {
      const key = `${r}:${c}`;
      if (key in prevRevs.current && cell.rev > prevRevs.current[key] && !cell.edited) changed[key] = true;
      prevRevs.current[key] = cell.rev;
    }));
    if (Object.keys(changed).length === 0) return undefined;
    setFlash((f) => ({ ...f, ...changed }));
    const t = setTimeout(() => setFlash((f) => {
      const n = { ...f };
      Object.keys(changed).forEach((k) => delete n[k]);
      return n;
    }), 2200);
    return () => clearTimeout(t);
  }, [grid]);

  // tell the server which rows are on screen so it refines those first
  useEffect(() => {
    if (!refining || !onFocusRows) return undefined;
    const el = scrollRef.current;
    if (!el) return undefined;
    let timer = null;
    const report = () => {
      const first = el.querySelector('tbody tr');
      const h = first ? first.offsetHeight || 34 : 34;
      const from = Math.max(1, Math.floor(el.scrollTop / h));
      const to = Math.ceil((el.scrollTop + el.clientHeight) / h) + 6;
      onFocusRows(table.table_index, from, to);
    };
    const onScroll = () => { clearTimeout(timer); timer = setTimeout(report, 500); };
    report();
    el.addEventListener('scroll', onScroll);
    return () => { clearTimeout(timer); el.removeEventListener('scroll', onScroll); };
  }, [refining, table.table_index, onFocusRows]);

  const startEdit = (r, c) => {
    if (saving) return;
    setSelected({ row: r, col: c });
    setEditing({ row: r, col: c });
    setDraft(grid[r][c].text);
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft('');
  };

  const saveValue = async (row, col, value, quiet = false) => {
    const res = await extractService.updateTableCell(jobId, table.table_index, row, col, value);
    if (res?.data?.table) onTableUpdated(table.table_index, res.data.table);
    if (!quiet) onToast?.(`Row ${row}, column ${col + 1} saved`, 'success');
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
      await saveValue(row, col, draft);
      cancelEdit();
    } catch (err) {
      onToast?.(`Could not save cell: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const useSuggestion = async (row, col) => {
    const s = grid[row][col].suggestion;
    if (!s || saving) return;
    setSaving(true);
    try {
      await saveValue(row, col, s);
    } catch (err) {
      onToast?.(`Could not save cell: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const applyAllSuggestions = async () => {
    if (!confirmApply) {
      setConfirmApply(true);
      setTimeout(() => setConfirmApply(false), 4000);
      return;
    }
    setConfirmApply(false);
    setSaving(true);
    let done = 0;
    try {
      for (let r = 1; r < grid.length; r += 1) {
        for (let c = 0; c < grid[r].length; c += 1) {
          const cell = grid[r][c];
          if (isLowConfidence(cell) && cell.suggestion) {
            // eslint-disable-next-line no-await-in-loop
            await saveValue(r, c, cell.suggestion, true);
            done += 1;
          }
        }
      }
      onToast?.(`${done} suggestion${done === 1 ? '' : 's'} applied`, 'success');
    } catch (err) {
      onToast?.(`Stopped after ${done}: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const jumpFlagged = (dir) => {
    if (flaggedList.length === 0) return;
    const cur = selected?.row ?? 0;
    const target = dir > 0
      ? (flaggedList.find((r) => r > cur) ?? flaggedList[0])
      : ([...flaggedList].reverse().find((r) => r < cur) ?? flaggedList[flaggedList.length - 1]);
    const col = Math.max(0, grid[target].findIndex((c) => isLowConfidence(c)));
    setSelected({ row: target, col });
    rowRefs.current[target]?.scrollIntoView({ block: 'center', behavior: 'smooth' });
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
        Table {tableNumber} was detected on page {pages[0]} but no cells could be read from it.
      </div>
    );
  }

  const sel = selected ? grid[selected.row]?.[selected.col] : null;
  const cc = summary.countCheck;
  const visibleRows = grid.slice(1).map((row, i) => ({ row, r: i + 1 })).filter(({ r }) => !onlyFlagged || flaggedList.includes(r));

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
      {/* Title bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 bg-slate-50 border-b border-slate-200">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <TableIcon size={15} className="text-blue-600 flex-shrink-0" />
          <span className="text-xs font-bold text-slate-800">Table {tableNumber}</span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">{pageLabel}</span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
            {bodyRows} {bodyRows === 1 ? 'row' : 'rows'} × {nCols} cols
          </span>
          {method && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200">{method}</span>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button onClick={handleCopy} className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer" title="Copy as tab-separated text (paste into Excel or Sheets)">
            {copied ? <Check size={11} className="text-emerald-600" /> : <Copy size={11} />} Copy
          </button>
          <button onClick={() => downloadText(`${baseName}.csv`, gridToCsv(grid), 'text/csv')} className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer" title="Download this table as CSV">
            <Download size={11} /> CSV
          </button>
          <button onClick={() => downloadText(`${baseName}.json`, JSON.stringify(gridToRecords(grid), null, 2), 'application/json')} className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:text-slate-900 hover:border-slate-300 cursor-pointer" title="Download this table as JSON (one object per row)">
            <FileJson size={11} /> JSON
          </button>
        </div>
      </div>

      {/* Summary line */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-slate-100 text-[11px]">
        <span className="inline-flex items-center gap-1 font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-800 border border-emerald-200">
          <CheckCircle2 size={11} /> {summary.accepted} accepted
        </span>
        <span className={`inline-flex items-center gap-1 font-bold px-2 py-0.5 rounded border ${summary.flagged ? 'bg-amber-50 text-amber-800 border-amber-300' : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
          <AlertTriangle size={11} /> {summary.flagged} to review
        </span>
        {cc && (
          <span
            className={`inline-flex items-center gap-1 font-semibold px-2 py-0.5 rounded border ${cc.ok ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-red-50 text-red-800 border-red-300'}`}
            title={cc.source ? `The document says: ${cc.source}` : 'Row count printed in the document'}
          >
            <ListChecks size={11} /> {cc.ok ? `Row count matches the document (${cc.expected})` : `Document says ${cc.expected} rows, ${cc.found} read`}
          </span>
        )}
        {summary.serial && (
          <span className={`font-mono px-2 py-0.5 rounded border ${summary.serial.gaps?.length ? 'bg-amber-50 text-amber-800 border-amber-300' : 'bg-slate-50 text-slate-600 border-slate-200'}`}
            title={summary.serial.gaps?.length ? `Serial numbers skip after ${summary.serial.gaps[0].after}` : 'Serial numbers run without gaps'}>
            Serial {summary.serial.first}–{summary.serial.last}{summary.serial.gaps?.length ? ` · ${summary.serial.gaps.length} gap${summary.serial.gaps.length > 1 ? 's' : ''}` : ''}
          </span>
        )}
        {summary.title && <span className="text-slate-400 truncate max-w-[24rem]" title={summary.title}>{summary.title}</span>}
      </div>

      {/* Background refinement banner */}
      {refining && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-blue-50 border-b border-blue-100 text-[11px] text-blue-900">
          <Loader2 size={12} className="animate-spin text-blue-600" />
          <span className="font-semibold">Refining in the background</span>
          <span className="text-blue-700">
            {refine.remaining > 0 ? `${refine.remaining} cells still to check` : 'checking cells'}
            {refine.improved > 0 ? ` · ${refine.improved} improved so far` : ''}. Rows you are viewing are read first.
          </span>
        </div>
      )}
      {refine && refine.status === 'done' && refine.improved > 0 && (
        <div className="px-4 py-1 bg-emerald-50/60 border-b border-emerald-100 text-[11px] text-emerald-800">
          Background refinement finished: {refine.improved} cells improved.
        </div>
      )}

      {/* Review toolbar */}
      {summary.flagged > 0 && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 border-b border-slate-100 bg-white text-[11px]">
          <button onClick={() => setOnlyFlagged((v) => !v)} className={`inline-flex items-center gap-1 font-semibold px-2 py-1 rounded border cursor-pointer ${onlyFlagged ? 'bg-amber-100 border-amber-300 text-amber-900' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'}`}>
            <Filter size={11} /> {onlyFlagged ? 'Showing rows to review' : 'Only rows to review'}
          </button>
          <button onClick={() => jumpFlagged(-1)} className="p-1 rounded border border-slate-200 text-slate-500 hover:text-slate-900 cursor-pointer" title="Previous row to review"><ChevronUp size={12} /></button>
          <button onClick={() => jumpFlagged(1)} className="p-1 rounded border border-slate-200 text-slate-500 hover:text-slate-900 cursor-pointer" title="Next row to review"><ChevronDown size={12} /></button>
          {summary.suggestions > 0 && (
            <button onClick={applyAllSuggestions} disabled={saving} className="inline-flex items-center gap-1 font-semibold px-2 py-1 rounded border border-blue-200 bg-blue-50 text-blue-800 hover:bg-blue-100 cursor-pointer disabled:opacity-60" title="Use the suggested value in every cell that has one">
              <Wand2 size={11} /> {confirmApply ? `Click again to apply ${summary.suggestions}` : `Apply ${summary.suggestions} suggestion${summary.suggestions > 1 ? 's' : ''}`}
            </button>
          )}
        </div>
      )}

      {/* Grid */}
      <div ref={scrollRef} className="overflow-auto max-h-[60vh]">
        <table className="border-collapse text-xs w-max min-w-full">
          <thead>
            <tr>
              <th className="sticky top-0 left-0 z-20 w-10 min-w-[2.5rem] bg-slate-100 border-b border-r border-slate-200 text-[10px] font-semibold text-slate-400 px-2 py-2 text-center">#</th>
              {grid[0].map((cell, c) => {
                const col = summary.columns.find((x) => x.index === c);
                const pc = summary.perCol[c];
                const headFlag = isLowConfidence(cell);
                return (
                  <th key={c} className="sticky top-0 z-10 bg-slate-100 border-b border-r border-slate-200 last:border-r-0 p-0 text-left align-top">
                    <div className="flex items-center gap-1 px-2.5 pt-1.5">
                      {col && col.kind && col.kind !== 'empty' && (
                        <span className="text-[9px] font-bold uppercase tracking-wide px-1 rounded bg-white text-slate-500 border border-slate-200">{KIND_LABEL[col.kind] || col.kind}</span>
                      )}
                      {pc?.flagged > 0 && <span className="text-[9px] font-bold px-1 rounded bg-amber-100 text-amber-800 border border-amber-200">{pc.flagged}</span>}
                    </div>
                    <GridCell
                      cell={cell} isHeader flagged={headFlag}
                      isEditing={editing?.row === 0 && editing?.col === c}
                      selected={selected?.row === 0 && selected?.col === c}
                      draft={draft} setDraft={setDraft}
                      onSelect={() => setSelected({ row: 0, col: c })}
                      onStart={() => startEdit(0, c)} onCommit={commitEdit} onCancel={cancelEdit}
                      saving={saving} placeholder={`Column ${c + 1}`}
                    />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map(({ row, r }) => (
              <tr key={r} ref={(el) => { rowRefs.current[r] = el; }} className="group/row hover:bg-blue-50/30">
                <td className="sticky left-0 z-10 bg-slate-50 group-hover/row:bg-blue-50 border-b border-r border-slate-200 text-[10px] font-mono text-slate-400 text-center px-2">{r}</td>
                {row.map((cell, c) => (
                  <td key={c} className="border-b border-r border-slate-100 last:border-r-0 p-0 align-top">
                    <GridCell
                      cell={cell}
                      flagged={isLowConfidence(cell)}
                      flashing={Boolean(flash[`${r}:${c}`])}
                      isEditing={editing?.row === r && editing?.col === c}
                      selected={selected?.row === r && selected?.col === c}
                      draft={draft} setDraft={setDraft}
                      onSelect={() => setSelected({ row: r, col: c })}
                      onStart={() => startEdit(r, c)} onCommit={commitEdit} onCancel={cancelEdit}
                      saving={saving}
                    />
                  </td>
                ))}
              </tr>
            ))}
            {bodyRows === 0 && (
              <tr><td colSpan={nCols + 1} className="px-4 py-3 text-center text-[11px] text-slate-400 italic">Only a header row was read for this table.</td></tr>
            )}
            {bodyRows > 0 && visibleRows.length === 0 && (
              <tr><td colSpan={nCols + 1} className="px-4 py-3 text-center text-[11px] text-emerald-700">Nothing left to review in this table.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Selected cell */}
      {sel && selected && (
        <CellDetail
          cell={sel} row={selected.row} col={selected.col} header={grid[0][selected.col]?.text}
          imageUrl={sel.bbox && jobId ? extractService.cellImageUrl(jobId, table.table_index, selected.row, selected.col, sel.rev) : null}
          saving={saving}
          onClose={() => setSelected(null)}
          onEdit={() => startEdit(selected.row, selected.col)}
          onUseSuggestion={() => useSuggestion(selected.row, selected.col)}
          onLocate={onLocateCell && sel.bbox ? () => onLocateCell({ page: sel.page || pages[0], bbox: sel.bbox, row: selected.row, col: selected.col }) : null}
        />
      )}

      <div className="px-4 py-1.5 bg-slate-50 border-t border-slate-200 text-[10px] text-slate-400 flex items-center gap-1.5">
        <Pencil size={10} /> Click a cell to see why it was flagged; double-click to correct it. Corrected cells are saved and never replaced by the background re-read.
      </div>
    </div>
  );
};

const GridCell = ({ cell, isHeader = false, flagged = false, flashing = false, selected = false, isEditing, draft, setDraft, onSelect, onStart, onCommit, onCancel, saving, placeholder }) => {
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

  const tone = flagged
    ? 'bg-amber-50 text-amber-900'
    : cell.edited
    ? 'bg-emerald-50/70 text-slate-900'
    : isHeader
    ? 'text-slate-800 font-bold'
    : 'text-slate-700';
  const changed = cell.rev > 0 && !cell.edited;
  const pct = cell.confidence != null ? Math.round(cell.confidence * 100) : null;
  const first = cell.reasons?.find((r) => r.level !== 'info') || cell.reasons?.[0];
  const title = flagged
    ? `${first ? first.message : 'Needs review'}${pct != null ? ` (${pct}%)` : ''}`
    : cell.edited ? 'Corrected by a reviewer' : changed && first ? first.message : 'Click for details, double-click to edit';

  return (
    <div
      onClick={onSelect}
      onDoubleClick={onStart}
      title={title}
      className={`relative px-2.5 py-2 min-w-[6rem] min-h-[2.2rem] font-mono cursor-pointer whitespace-pre-wrap break-words hover:bg-blue-50 transition-colors ${tone} ${selected ? 'ring-2 ring-inset ring-blue-500' : ''} ${flashing ? 'bg-blue-100' : ''}`}
    >
      {cell.text !== '' ? cell.text : <span className="text-slate-300">{isHeader ? placeholder : ''}</span>}
      {flagged && <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-amber-500" />}
      {cell.edited && !flagged && <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-emerald-500" />}
      {changed && !flagged && !cell.edited && <Wand2 size={9} className="absolute top-1 right-1 text-blue-400" />}
      {!isHeader && cell.text !== '' && pct != null && (
        <span className="absolute left-0 right-0 bottom-0 h-[3px] bg-slate-100">
          <span className={`block h-full ${confColor(cell.confidence, flagged)}`} style={{ width: `${Math.max(4, pct)}%` }} />
        </span>
      )}
    </div>
  );
};

const CellDetail = ({ cell, row, col, header, imageUrl, saving, onClose, onEdit, onUseSuggestion, onLocate }) => {
  const [imgOk, setImgOk] = useState(true);
  useEffect(() => { setImgOk(true); }, [imageUrl]);
  const flagged = isLowConfidence(cell);
  const pct = cell.confidence != null ? Math.round(cell.confidence * 100) : null;
  const ocrPct = cell.ocrConfidence != null ? Math.round(cell.ocrConfidence * 100) : null;
  return (
    <div className="border-t border-slate-200 bg-slate-50/80 px-4 py-3 flex flex-wrap gap-4 items-start">
      <div className="min-w-[10rem]">
        <div className="text-[10px] font-bold uppercase tracking-wide text-slate-400 mb-1">
          {row === 0 ? 'Header' : `Row ${row}`} · {header || `Column ${col + 1}`}
        </div>
        {imageUrl && imgOk ? (
          <img src={imageUrl} alt="Close-up of the cell on the page" onError={() => setImgOk(false)}
            className="max-h-24 max-w-[22rem] border border-slate-300 rounded bg-white shadow-sm" />
        ) : (
          <div className="text-[11px] text-slate-400 italic">No close-up available</div>
        )}
      </div>

      <div className="flex-1 min-w-[14rem] space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-bold text-slate-900 break-all">{cell.text || '(empty)'}</span>
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${flagged ? 'bg-amber-50 text-amber-800 border-amber-300' : cell.edited ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-emerald-50 text-emerald-800 border-emerald-200'}`}>
            {cell.edited ? 'Corrected by you' : flagged ? 'To review' : 'Accepted'}
          </span>
          {pct != null && <span className="text-[10px] font-mono text-slate-500">fit {pct}%{ocrPct != null ? ` · read ${ocrPct}%` : ''}</span>}
        </div>
        {cell.rawText != null && cell.rawText !== cell.text && (
          <div className="text-[11px] text-slate-500">First read as <span className="font-mono text-slate-700">{cell.rawText || '(nothing)'}</span></div>
        )}
        {cell.reasons?.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {cell.reasons.map((r, i) => (
              <span key={i} className={`text-[10px] px-1.5 py-0.5 rounded border ${r.level === 'bad' ? 'bg-red-50 text-red-800 border-red-200' : r.level === 'warn' ? 'bg-amber-50 text-amber-800 border-amber-200' : 'bg-blue-50 text-blue-800 border-blue-200'}`}>
                {r.message}
              </span>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
          {cell.suggestion && flagged && (
            <button onClick={onUseSuggestion} disabled={saving} className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-blue-300 bg-blue-600 text-white hover:bg-blue-700 cursor-pointer disabled:opacity-60">
              <Wand2 size={11} /> Use “{cell.suggestion}”
            </button>
          )}
          <button onClick={onEdit} className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-300 bg-white text-slate-700 hover:border-slate-400 cursor-pointer">
            <Pencil size={11} /> Edit
          </button>
          {onLocate && (
            <button onClick={onLocate} className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded border border-slate-300 bg-white text-slate-700 hover:border-slate-400 cursor-pointer">
              <MapPin size={11} /> Show on page
            </button>
          )}
        </div>
      </div>

      <button onClick={onClose} className="p-1 text-slate-400 hover:text-slate-700 cursor-pointer" title="Close"><X size={14} /></button>
    </div>
  );
};

export default TableGrid;
