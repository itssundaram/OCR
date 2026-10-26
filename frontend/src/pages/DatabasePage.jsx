import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Database, Plug, Unplug, Table2, Eye, RefreshCw, ChevronLeft, ChevronRight, Download, Save, Trash2, ShieldAlert, Search, Hash } from 'lucide-react';
import { dbAdminService } from '../services/dbAdmin';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const EMPTY = { dialect: 'oracle', host: 'localhost', port: '1521', service_name: '', sid: '', database: '', username: '', password: '', path: '' };
const DEFAULT_PORT = { oracle: '1521', postgresql: '5432', sqlite: '' };
const inputCls = 'w-full border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-200';
const btnCls = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold border transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

const Field = ({ label, children }) => (
  <label className="block text-xs font-semibold text-slate-600 mb-2">{label}<div className="mt-1 font-normal">{children}</div></label>
);

const fmtCell = (v) => (v === null ? <span className="italic text-slate-400">NULL</span> : typeof v === 'boolean' ? String(v) : String(v));

const toCsv = (cols, rows) => {
  const esc = (v) => (v === null ? '' : `"${String(v).replace(/"/g, '""')}"`);
  return [cols.map(esc).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\r\n');
};

export default function DatabasePage() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(EMPTY);
  const [profiles, setProfiles] = useState([]);
  const [profileId, setProfileId] = useState('');
  const [saveAs, setSaveAs] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [conn, setConn] = useState(null); // { session_id, label, server_version, dialect, connect_ms }
  const [schemas, setSchemas] = useState({ schemas: [], default: null });
  const [includeSystem, setIncludeSystem] = useState(false);
  const [schema, setSchema] = useState('');
  const [tables, setTables] = useState([]);
  const [filter, setFilter] = useState('');
  const [table, setTable] = useState('');
  const [tab, setTab] = useState('data');
  const [cols, setCols] = useState(null);
  const [data, setData] = useState(null);
  const [pageSize, setPageSize] = useState(100);
  const [offset, setOffset] = useState(0);
  const [orderBy, setOrderBy] = useState('');
  const [desc, setDesc] = useState(false);
  const [exact, setExact] = useState(null);
  const [browseError, setBrowseError] = useState('');

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const sid = conn?.session_id;

  const loadStatus = useCallback(async () => {
    try {
      const r = await dbAdminService.status();
      setStatus(r.data);
      if (r.data.allowed) setProfiles((await dbAdminService.profiles()).data || []);
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { loadStatus(); }, [loadStatus]);

  const changeDialect = (e) => {
    const dialect = e.target.value;
    setForm((f) => ({ ...f, dialect, port: DEFAULT_PORT[dialect] }));
  };

  const pickProfile = (id) => {
    setProfileId(id);
    const p = profiles.find((x) => x.id === id);
    if (p) { setForm({ ...EMPTY, ...p.params, port: p.params.port ? String(p.params.port) : '', password: '' }); setSaveAs(p.name); }
  };

  const connect = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const params = { ...form, port: form.port ? Number(form.port) : null };
      const r = await dbAdminService.connect({ profileId: profileId || null, params, saveAs: saveAs.trim() || null });
      setTable(''); setCols(null); setData(null); setTables([]); setSchema(''); setSchemas({ schemas: [], default: null });
      setConn(r.data);                                       // the schema list loads in the effect below
      if (saveAs.trim()) loadStatus();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const disconnect = async () => {
    if (sid) { try { await dbAdminService.disconnect(sid); } catch { /* already gone */ } }
    setConn(null); setTables([]); setTable(''); setCols(null); setData(null); setSchema(''); setSchemas({ schemas: [], default: null });
  };

  // an expired session (idle too long) sends the page back to the form instead of failing silently
  const guarded = useCallback(async (fn) => {
    try { return await fn(); } catch (e) {
      if (e.code === 'DB_SESSION_NOT_FOUND') { setConn(null); setError(e.message); } else setBrowseError(e.message);
      return null;
    }
  }, []);

  useEffect(() => {
    if (!sid) return;
    (async () => {
      setBrowseError('');
      const sc = await guarded(() => dbAdminService.schemas(sid, includeSystem));
      if (sc) { setSchemas(sc.data); setSchema((cur) => cur || sc.data.default || sc.data.schemas[0] || ''); }
    })();
  }, [sid, includeSystem, guarded]);

  useEffect(() => {
    if (!sid || !schema) return;                              // wait until the schema list has chosen one
    (async () => {
      setBrowseError(''); setTable(''); setCols(null); setData(null);
      const t = await guarded(() => dbAdminService.tables(sid, schema || undefined));
      setTables(t ? t.data : []);
    })();
  }, [sid, schema, guarded]);

  const rowsSeq = useRef(0);
  const loadRows = useCallback(async (tbl, off, size, ob, d) => {
    const mine = ++rowsSeq.current;                       // only the newest request may paint (fast double clicks can answer out of order)
    const r = await guarded(() => dbAdminService.rows(sid, schema || undefined, tbl, { limit: size, offset: off, orderBy: ob || undefined, desc: d }));
    if (r && mine === rowsSeq.current) { setData(r.data); setBrowseError(''); }
  }, [sid, schema, guarded]);

  const openTable = async (name) => {
    setTable(name); setOffset(0); setOrderBy(''); setDesc(false); setExact(null); setCols(null); setData(null);
    const c = await guarded(() => dbAdminService.columns(sid, schema || undefined, name));
    if (c) setCols(c.data);
    await loadRows(name, 0, pageSize, '', false);
  };

  const goPage = (off) => { const o = Math.max(0, off); setOffset(o); loadRows(table, o, pageSize, orderBy, desc); };
  const sortBy = (col) => {
    const d = orderBy === col ? !desc : false;
    setOrderBy(col); setDesc(d); setOffset(0); loadRows(table, 0, pageSize, col, d);
  };
  const changeSize = (e) => { const n = Number(e.target.value); setPageSize(n); setOffset(0); loadRows(table, 0, n, orderBy, desc); };
  const countExact = async () => {
    const r = await guarded(() => dbAdminService.count(sid, schema || undefined, table));
    if (r) setExact(r.data.count);
  };
  const downloadCsv = () => {
    const blob = new Blob([toCsv(data.columns, data.rows)], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `${table}_${offset + 1}-${offset + data.rows.length}.csv`; a.click();
    URL.revokeObjectURL(a.href);
  };

  const saveOnly = async () => {
    setError(''); setNotice('');
    try {
      await dbAdminService.saveProfile(saveAs.trim(), { ...form, port: form.port ? Number(form.port) : null }, profileId || null);
      setNotice('Connection saved.'); loadStatus();
    } catch (e) { setError(e.message); }
  };
  const deleteProfile = async () => {
    if (!profileId || !window.confirm('Delete this saved connection?')) return;
    try { await dbAdminService.deleteProfile(profileId); setProfileId(''); setNotice('Saved connection deleted.'); loadStatus(); } catch (e) { setError(e.message); }
  };

  const useAsApp = async () => {
    if (!window.confirm(`Make "${conn.label}" the database DOCINT itself uses?\n\nThe API and all workers must be restarted for this to take effect. The database must already contain the DOCINT tables.`)) return;
    try {
      const r = await dbAdminService.useAsAppDb(sid);
      setNotice(`Saved. Restart the API and run_worker.py to switch to ${r.data.label}.`); loadStatus();
    } catch (e) { setError(e.message); }
  };
  const clearOverride = async () => {
    if (!window.confirm('Go back to the database set in .env? (restart needed)')) return;
    try { await dbAdminService.clearOverride(); setNotice('Override removed. Restart the API and run_worker.py.'); loadStatus(); } catch (e) { setError(e.message); }
  };

  const shown = useMemo(() => tables.filter((t) => t.name.toLowerCase().includes(filter.toLowerCase())), [tables, filter]);
  const maxRows = status?.max_rows || 500;
  const sizes = [25, 50, 100, 200, 500].filter((n) => n <= maxRows);

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;

  if (!status?.enabled || !status?.allowed) {
    return (
      <div className="max-w-2xl mx-auto p-6" data-testid="db-disabled">
        <Card><CardContent className="flex gap-3 items-start">
          <ShieldAlert className="text-amber-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-slate-700">
            {!status?.enabled
              ? <><b>The Database page is switched off.</b><p className="mt-2">DOCINT has no login, so this page stays off until you turn it on. In <code>backend/.env</code> set <code>DB_ADMIN_UI_ENABLED=true</code>, then restart the API.</p></>
              : <><b>This computer ({status.client}) may not use the Database page.</b><p className="mt-2">By default only the machine running the API can. To allow another address, add it to <code>DB_ADMIN_ALLOWED_HOSTS</code> in <code>backend/.env</code> and restart the API.</p></>}
          </div>
        </CardContent></Card>
      </div>
    );
  }

  const app = status.app_database || {};
  const d = form.dialect;

  return (
    <div className="p-4 space-y-4 max-w-[1500px] mx-auto" data-testid="db-page">
      <div className="flex items-center gap-3">
        <Database className="text-blue-900" />
        <h2 className="text-xl font-bold text-slate-900 m-0">Database</h2>
        <span className="text-xs text-slate-500">Read-only browser. No SQL is typed or run; passwords stay on the server.</span>
      </div>

      {(error || notice) && (
        <div className={`rounded-lg px-3 py-2 text-sm border ${error ? 'bg-red-50 border-red-200 text-red-800' : 'bg-emerald-50 border-emerald-200 text-emerald-800'}`} data-testid="db-message">
          {error || notice}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-4">
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Connection</CardTitle></CardHeader>
            <CardContent>
              {conn ? (
                <div className="space-y-3 text-sm" data-testid="db-connected">
                  <div className="flex items-center gap-2"><Badge variant="success">connected</Badge><span className="font-mono text-xs break-all">{conn.label}</span></div>
                  <div className="text-xs text-slate-500 break-words">{conn.server_version || 'version unknown'} · {conn.connect_ms} ms</div>
                  <div className="flex flex-wrap gap-2">
                    <button className={`${btnCls} border-slate-300 hover:bg-slate-50`} onClick={disconnect}><Unplug size={14} />Disconnect</button>
                    <button className={`${btnCls} border-amber-300 text-amber-800 hover:bg-amber-50`} onClick={useAsApp} title="Needs a restart">Use as app database</button>
                  </div>
                </div>
              ) : (
                <div>
                  {profiles.length > 0 && (
                    <Field label="Saved connections">
                      <select className={inputCls} value={profileId} onChange={(e) => pickProfile(e.target.value)} data-testid="db-profile">
                        <option value="">— new connection —</option>
                        {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.has_password ? '' : ' (no password)'}</option>)}
                      </select>
                    </Field>
                  )}
                  <Field label="Database type">
                    <select className={inputCls} value={d} onChange={changeDialect} data-testid="db-dialect">
                      <option value="oracle" disabled={status.drivers && !status.drivers.oracle}>Oracle</option>
                      <option value="postgresql" disabled={status.drivers && !status.drivers.postgresql}>PostgreSQL{status.drivers && !status.drivers.postgresql ? ' (driver not installed)' : ''}</option>
                      <option value="sqlite">SQLite file</option>
                    </select>
                  </Field>
                  {d !== 'sqlite' && (
                    <div className="grid grid-cols-[1fr_90px] gap-2">
                      <Field label="Host"><input className={inputCls} value={form.host} onChange={set('host')} /></Field>
                      <Field label="Port"><input className={inputCls} value={form.port} onChange={set('port')} inputMode="numeric" /></Field>
                    </div>
                  )}
                  {d === 'oracle' && (<>
                    <Field label="Service name"><input className={inputCls} value={form.service_name} onChange={set('service_name')} placeholder="FREEPDB1" /></Field>
                    <Field label="…or SID (only if no service name)"><input className={inputCls} value={form.sid} onChange={set('sid')} /></Field>
                  </>)}
                  {d === 'postgresql' && <Field label="Database"><input className={inputCls} value={form.database} onChange={set('database')} /></Field>}
                  {d === 'sqlite' && <Field label="File path on the server"><input className={inputCls} value={form.path} onChange={set('path')} placeholder="C:\data\docint.db" /></Field>}
                  {d !== 'sqlite' && (<>
                    <Field label="User"><input className={inputCls} value={form.username} onChange={set('username')} autoComplete="off" /></Field>
                    <Field label={profileId ? 'Password (leave empty to use the saved one)' : 'Password'}>
                      <input className={inputCls} type="password" value={form.password} onChange={set('password')} autoComplete="new-password" data-testid="db-password" />
                    </Field>
                  </>)}
                  <Field label="Save as (optional)">
                    <input className={inputCls} value={saveAs} onChange={(e) => setSaveAs(e.target.value)} placeholder="e.g. Production Oracle"
                      disabled={!status.can_store_passwords && d !== 'sqlite'} />
                  </Field>
                  {!status.can_store_passwords && d !== 'sqlite' && <p className="text-xs text-amber-700 mb-2">Passwords cannot be saved on this server (missing the cryptography package).</p>}
                  <div className="flex flex-wrap gap-2 mt-1">
                    <button className={`${btnCls} bg-blue-900 text-white border-blue-900 hover:bg-blue-800`} onClick={connect} disabled={busy} data-testid="db-connect">
                      <Plug size={14} />{busy ? 'Connecting…' : 'Connect'}
                    </button>
                    {saveAs.trim() && <button className={`${btnCls} border-slate-300 hover:bg-slate-50`} onClick={saveOnly}><Save size={14} />Save only</button>}
                    {profileId && <button className={`${btnCls} border-red-200 text-red-700 hover:bg-red-50`} onClick={deleteProfile}><Trash2 size={14} />Delete</button>}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>The app's own database</CardTitle></CardHeader>
            <CardContent className="text-sm space-y-2">
              <div className="font-mono text-xs break-all">{app.dialect || '?'} · {app.user ? `${app.user}@` : ''}{app.host || ''}{app.port ? `:${app.port}` : ''}{app.database ? `/${app.database}` : ''}</div>
              {status.override
                ? <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2">An override is saved ({status.override.label}); it applies after the next restart and replaces the value in .env.
                    <button className="underline ml-1" onClick={clearOverride}>Remove it</button></div>
                : <div className="text-xs text-slate-500">Set in <code>.env</code>.</div>}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4 min-w-0">
          {!conn ? (
            <Card><CardContent className="text-sm text-slate-500 py-10 text-center">Connect to a database to see its tables.</CardContent></Card>
          ) : (
            <div className="grid grid-cols-1 xl:grid-cols-[300px_1fr] gap-4">
              <Card className="self-start">
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <select className={inputCls} value={schema} onChange={(e) => setSchema(e.target.value)} data-testid="db-schema">
                      {schemas.schemas.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  {schemas.schemas.length > 1 || includeSystem ? (
                    <label className="flex items-center gap-1.5 text-xs text-slate-500 mt-2"><input type="checkbox" checked={includeSystem} onChange={(e) => setIncludeSystem(e.target.checked)} />show system schemas</label>
                  ) : null}
                  <div className="relative mt-2"><Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
                    <input className={`${inputCls} pl-8`} placeholder="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
                </CardHeader>
                <CardContent className="p-0 max-h-[60vh] overflow-auto">
                  {shown.length === 0 && <div className="p-4 text-sm text-slate-500">No tables.</div>}
                  <ul className="m-0 p-0 list-none" data-testid="db-tables">
                    {shown.map((t) => (
                      <li key={t.name}>
                        <button onClick={() => openTable(t.name)} className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 border-b border-slate-100 hover:bg-slate-50 ${t.name === table ? 'bg-blue-50 font-semibold' : ''}`}>
                          {t.kind === 'view' ? <Eye size={14} className="text-slate-400" /> : <Table2 size={14} className="text-slate-400" />}
                          <span className="truncate flex-1">{t.name}</span>
                          {t.approx_rows != null && <span className="text-[0.7rem] text-slate-400">~{t.approx_rows.toLocaleString()}</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>

              <Card className="min-w-0">
                {!table ? <CardContent className="text-sm text-slate-500 py-10 text-center">Pick a table.</CardContent> : (<>
                  <CardHeader>
                    <div className="flex flex-wrap items-center gap-2">
                      <CardTitle className="font-mono">{schema ? `${schema}.` : ''}{table}</CardTitle>
                      <div className="ml-auto flex gap-1">
                        {['data', 'columns'].map((k) => (
                          <button key={k} onClick={() => setTab(k)} className={`${btnCls} ${tab === k ? 'bg-slate-900 text-white border-slate-900' : 'border-slate-300 hover:bg-slate-50'}`}>{k === 'data' ? 'Data' : 'Columns'}</button>
                        ))}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {browseError && <div className="rounded-lg px-3 py-2 text-sm border bg-red-50 border-red-200 text-red-800">{browseError}</div>}
                    {tab === 'columns' && cols && (
                      <div className="overflow-auto" data-testid="db-columns">
                        <table className="w-full text-sm border-collapse">
                          <thead><tr className="text-left text-xs text-slate-500"><th className="p-2">Column</th><th className="p-2">Type</th><th className="p-2">Null</th><th className="p-2">Key</th><th className="p-2">Default</th></tr></thead>
                          <tbody>{cols.columns.map((c) => (
                            <tr key={c.name} className="border-t border-slate-100">
                              <td className="p-2 font-mono">{c.name}</td><td className="p-2 text-slate-600">{c.type}</td>
                              <td className="p-2">{c.nullable ? 'yes' : 'no'}</td><td className="p-2">{c.primary_key && <Badge variant="info">PK</Badge>}</td>
                              <td className="p-2 text-xs text-slate-500">{c.default}</td>
                            </tr>))}
                          </tbody>
                        </table>
                        {cols.foreign_keys.length > 0 && (
                          <div className="mt-3 text-xs text-slate-600">
                            <b>Foreign keys</b>
                            {cols.foreign_keys.map((f, i) => <div key={i} className="font-mono">{(f.columns || []).join(', ')} → {f.ref_schema ? `${f.ref_schema}.` : ''}{f.ref_table}({(f.ref_columns || []).join(', ')})</div>)}
                          </div>
                        )}
                      </div>
                    )}
                    {tab === 'data' && (data ? (<>
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <button className={`${btnCls} border-slate-300 hover:bg-slate-50`} onClick={() => goPage(offset - pageSize)} disabled={offset === 0} aria-label="Previous page"><ChevronLeft size={14} /></button>
                        <span data-testid="db-range">rows {data.rows.length ? offset + 1 : 0}–{offset + data.rows.length}{exact != null ? ` of ${exact.toLocaleString()}` : ''}</span>
                        <button className={`${btnCls} border-slate-300 hover:bg-slate-50`} onClick={() => goPage(offset + pageSize)} disabled={!data.has_more} aria-label="Next page"><ChevronRight size={14} /></button>
                        <select className="border border-slate-300 rounded-lg px-2 py-1 text-sm" value={pageSize} onChange={changeSize}>{sizes.map((n) => <option key={n} value={n}>{n} rows</option>)}</select>
                        <button className={`${btnCls} border-slate-300 hover:bg-slate-50`} onClick={countExact} title="Counts every row (can be slow on big tables)"><Hash size={14} />Exact count</button>
                        <button className={`${btnCls} border-slate-300 hover:bg-slate-50`} onClick={() => loadRows(table, offset, pageSize, orderBy, desc)}><RefreshCw size={14} />Refresh</button>
                        <button className={`${btnCls} border-slate-300 hover:bg-slate-50 ml-auto`} onClick={downloadCsv}><Download size={14} />CSV (this page)</button>
                      </div>
                      <div className="overflow-auto max-h-[60vh] border border-slate-200 rounded-lg">
                        <table className="text-sm border-collapse min-w-full" data-testid="db-rows">
                          <thead className="sticky top-0 bg-slate-100">
                            <tr>{data.columns.map((c) => (
                              <th key={c} className="text-left p-2 font-mono text-xs whitespace-nowrap cursor-pointer select-none hover:bg-slate-200" onClick={() => sortBy(c)}>
                                {c}{orderBy === c ? (desc ? ' ▼' : ' ▲') : ''}
                              </th>))}
                            </tr>
                          </thead>
                          <tbody>{data.rows.map((r, i) => (
                            <tr key={i} className="border-t border-slate-100 odd:bg-white even:bg-slate-50/50">
                              {r.map((v, j) => <td key={j} className="p-2 align-top max-w-[360px] truncate font-mono text-xs" title={v === null ? 'NULL' : String(v)}>{fmtCell(v)}</td>)}
                            </tr>))}
                          </tbody>
                        </table>
                        {data.rows.length === 0 && <div className="p-4 text-sm text-slate-500">No rows.</div>}
                      </div>
                    </>) : <Loader text="Loading rows..." />)}
                  </CardContent>
                </>)}
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
