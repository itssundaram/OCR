/**
 * DOCINT — Generate URL page
 * Creates shareable department/template pre-filled links into the OCR page,
 * backed by app/api/routes/department_urls.py.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Link2, Copy, Check, Trash2, Ban, Plus } from 'lucide-react';
import { departmentsService } from '../services/departments';
import { templatesService } from '../services/templates';
import { departmentUrlsService } from '../services/departmentUrls';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const GenerateUrlPage = () => {
  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [links, setLinks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const [form, setForm] = useState({ department_id: '', template_id: '', label: '' });
  const [creating, setCreating] = useState(false);

  const loadLinks = useCallback(async () => {
    try {
      const res = await departmentUrlsService.list();
      setLinks(res.data || []);
    } catch (err) {
      setError(err.message || 'Failed to load generated links');
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const deptRes = await departmentsService.list();
        const active = deptRes.data.filter((d) => d.is_active);
        setDepartments(active);
        if (active.length > 0) setForm((f) => ({ ...f, department_id: active[0].id }));
        await loadLinks();
      } catch (err) {
        setError(err.message || 'Failed to load data');
      } finally {
        setLoading(false);
      }
    })();
  }, [loadLinks]);

  useEffect(() => {
    if (!form.department_id) { setTemplates([]); return; }
    const dept = departments.find((d) => d.id === Number(form.department_id));
    if (!dept) return;
    templatesService.listTemplates(dept.slug).then((res) => {
      setTemplates((res.data || []).filter((t) => t.is_active));
    }).catch(() => setTemplates([]));
  }, [form.department_id, departments]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      await departmentUrlsService.create({
        department_id: Number(form.department_id),
        template_id: form.template_id ? Number(form.template_id) : null,
        label: form.label || null,
      });
      setForm((f) => ({ ...f, label: '' }));
      await loadLinks();
    } catch (err) {
      setError(err.message || 'Failed to generate link');
    } finally {
      setCreating(false);
    }
  };

  const fullUrl = (relative) => `${window.location.origin}${relative}`;

  const copyLink = (row) => {
    navigator.clipboard.writeText(fullUrl(row.url));
    setCopiedId(row.id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  const withBusy = async (id, fn) => {
    setBusyId(id);
    try {
      await fn();
      await loadLinks();
    } catch (err) {
      setError(err.message || 'Action failed');
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;

  return (
    <div className="flex flex-col gap-6 max-w-6xl mx-auto p-4 w-full h-full">
      <div className="flex flex-col text-center mt-2 mb-2">
        <h2 className="text-3xl font-bold text-primary font-heading mb-2">Generate URL</h2>
        <p className="text-secondary max-w-lg mx-auto">
          Create a shareable link pre-filled with a department and template, so a team can jump straight to the OCR page.
        </p>
      </div>

      <Card className="animate-fade-in-up border border-primary-accent border-opacity-20 shadow-lg">
        <CardContent className="p-6">
          <form onSubmit={handleCreate} className="flex flex-col gap-4">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--spacing-lg)' }}>
              <div className="input-group mb-0">
                <label className="input-label">Department</label>
                <select className="input-field" value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value, template_id: '' })} required>
                  <option value="" disabled>Select department...</option>
                  {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              <div className="input-group mb-0">
                <label className="input-label">Template (optional)</label>
                <select className="input-field" value={form.template_id} onChange={(e) => setForm({ ...form, template_id: e.target.value })}>
                  <option value="">Any template</option>
                  {templates.map((t) => <option key={t.id} value={t.id}>{t.code}</option>)}
                </select>
              </div>
              <div className="input-group mb-0">
                <label className="input-label">Label (optional)</label>
                <input className="input-field" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="e.g. Finance team intake" />
              </div>
            </div>
            <div className="flex justify-end">
              <button type="submit" className="btn btn-primary flex items-center gap-2" disabled={creating || !form.department_id}>
                <Plus size={16} /> {creating ? 'Generating...' : 'Generate Link'}
              </button>
            </div>
          </form>
        </CardContent>
      </Card>

      {error && (
        <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger text-sm">{error}</div>
      )}

      <Card className="flex-1 animate-fade-in-up" style={{ animationDelay: '0.1s' }}>
        <CardHeader><CardTitle>Generated Links</CardTitle></CardHeader>
        <CardContent className="p-0">
          {links.length === 0 ? (
            <div className="p-8 text-center text-secondary italic">No links generated yet.</div>
          ) : (
            <div className="flex flex-col">
              {links.map((row) => (
                <div key={row.id} className="flex items-center justify-between gap-4 px-4 py-3 border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    <Link2 size={16} className="text-primary-accent shrink-0" />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-primary">{row.label || `${row.department_name} link`}</span>
                        {!row.is_active && <Badge variant="default">Inactive</Badge>}
                        {row.template_code && <Badge variant="info">{row.template_code}</Badge>}
                      </div>
                      <div className="text-xs text-tertiary font-mono truncate" style={{ maxWidth: '480px' }}>{fullUrl(row.url)}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button className="btn btn-secondary text-xs flex items-center gap-1" onClick={() => copyLink(row)}>
                      {copiedId === row.id ? <Check size={14} /> : <Copy size={14} />} {copiedId === row.id ? 'Copied' : 'Copy'}
                    </button>
                    {row.is_active && (
                      <button className="btn btn-secondary text-xs flex items-center gap-1" disabled={busyId === row.id} onClick={() => withBusy(row.id, () => departmentUrlsService.deactivate(row.id))}>
                        <Ban size={14} /> Deactivate
                      </button>
                    )}
                    <button
                      className="btn btn-secondary text-xs text-danger flex items-center gap-1"
                      disabled={busyId === row.id}
                      onClick={() => {
                        if (window.confirm('Delete this link permanently?')) withBusy(row.id, () => departmentUrlsService.remove(row.id));
                      }}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default GenerateUrlPage;
