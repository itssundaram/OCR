import React, { useMemo, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, ListTree } from 'lucide-react';

/**
 * Structured per-row records of a table (table.meta.records, built by the backend knowledge layer):
 * one object per row, keyed by canonical column ids ("pers_no", "rank", "name", "unit", "sus_no" ...), with the
 * combined cells already split and the dates written dd-mm-yyyy. Shown under the editable grid, read-only.
 */
const LABELS = {
  sno: 'S No', pers_no: 'Pers No', rank: 'Rank', name: 'Name', rank_name: 'Rank & Name', doc: 'DOC', dos: 'DOS',
  dor: 'DOR', dob: 'DOB', tos: 'TOS', unit: 'Unit', sus_no: 'SUS No', arms: 'Arms', gender: 'Gender',
  cat_part_no: 'Cat/Part No', nomenclature: 'Nomenclature', regd_nos: 'Regd Nos', au: 'AU', qty: 'Qty',
  uh_qty: 'UH Qty', demand_qty: 'Demand Qty', remarks: 'Remarks',
};

export const labelOf = (key) =>
  LABELS[key] || String(key).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const TableRecords = ({ table }) => {
  const records = Array.isArray(table?.meta?.records) ? table.meta.records : [];
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const columns = useMemo(() => {
    const seen = [];
    records.forEach((r) => Object.keys(r || {}).forEach((k) => { if (!seen.includes(k)) seen.push(k); }));
    return seen.filter((k) => records.some((r) => String(r?.[k] ?? '').trim() !== ''));
  }, [records]);

  if (records.length === 0 || columns.length === 0) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(records, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be blocked on an http page; the table stays selectable */
    }
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-xs overflow-hidden" data-testid="table-records">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 cursor-pointer"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <ListTree size={14} className="text-blue-600" />
        Structured records
        <span className="font-normal text-slate-400">{records.length} row{records.length === 1 ? '' : 's'} · {columns.length} fields</span>
      </button>
      {open && (
        <div className="border-t border-slate-200">
          <div className="flex justify-end px-3 pt-2">
            <button
              type="button"
              onClick={copy}
              className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-600 hover:text-blue-700 cursor-pointer"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? 'Copied' : 'Copy JSON'}
            </button>
          </div>
          <div className="overflow-auto max-h-80 p-3 pt-1">
            <table className="min-w-full text-[11px] border-collapse">
              <thead>
                <tr>
                  {columns.map((k) => (
                    <th key={k} className="sticky top-0 bg-slate-100 text-left font-bold text-slate-600 px-2 py-1 border border-slate-200 whitespace-nowrap">
                      {labelOf(k)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {records.map((r, i) => (
                  <tr key={i} className="odd:bg-white even:bg-slate-50/60">
                    {columns.map((k) => (
                      <td key={k} className="px-2 py-1 border border-slate-200 whitespace-nowrap text-slate-800">
                        {String(r?.[k] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default TableRecords;
