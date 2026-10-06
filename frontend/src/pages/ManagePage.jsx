/**
 * DOCINT — Manage Departments & Templates
 * One page combining both CRUD surfaces, since they're tightly coupled
 * (a template always belongs to a department). Template creation accepts
 * the raw template_json (fields array) — the visual bounding-box field
 * builder is tracked separately (Phase 21 follow-up) and not built here.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Layers, Building2, Plus, CheckCircle2, XCircle, Trash2, RefreshCw, X,
} from 'lucide-react';
import { departmentsService } from '../services/departments';
import { templatesService } from '../services/templates';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Loader } from '../components/ui/Loader';

const EXAMPLE_TEMPLATE_JSON = `{
  "fields": [
    { "name": "invoice_number", "type": "text", "description": "The invoice number printed on the document", "required": true }
  ]
}`;

const Modal = ({ title, onClose, children }) => (
  <div
    style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.4)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }}
    onClick={onClose}
  >
    <div
      className="glass-panel animate-fade-in-up"
      style={{ width: '100%', maxWidth: '560px', margin: '1rem', padding: '1.5rem', maxHeight: '85vh', overflowY: 'auto' }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-bold text-primary">{title}</h3>
        <button onClick={onClose} className="icon-btn"><X size={20} /></button>
      </div>
      {children}
    </div>
  </div>
);

const ManagePage = () => {
  const [tab, setTab] = useState('departments'); // departments | templates
  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [selectedDeptFilter, setSelectedDeptFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const [showDeptModal, setShowDeptModal] = useState(false);
  const [deptForm, setDeptForm] = useState({ slug: '', name: '', description: '', color: '#6366f1' });

  const [showTemplateModal, setShowTemplateModal] = useState(false);
  const [templateForm, setTemplateForm] = useState({ department_slug: '', code: '', extraction_instructions: '', template_json: EXAMPLE_TEMPLATE_JSON });

  const deptNameById = (id) => departments.find((d) => d.id === id)?.name || `#${id}`;

  const loadAll = useCallback(async (deptSlug) => {
    setLoading(true);
    setError(null);
    try {
      const [deptRes, templRes] = await Promise.all([
        departmentsService.listAll(),
        templatesService.listTemplates(deptSlug || undefined),
      ]);
      setDepartments(deptRes.data || []);
      setTemplates(templRes.data || []);
    } catch (err) {
      setError(err.message || 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(''); }, [loadAll]);

  const handleDeptFilterChange = (slug) => {
    setSelectedDeptFilter(slug);
    loadAll(slug);
  };

  const withBusy = async (id, fn) => {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      await loadAll(selectedDeptFilter);
    } catch (err) {
      setError(err.message || 'Action failed');
    } finally {
      setBusyId(null);
    }
  };

  const submitDept = async (e) => {
    e.preventDefault();
    try {
      await departmentsService.create(deptForm);
      setShowDeptModal(false);
      setDeptForm({ slug: '', name: '', description: '', color: '#6366f1' });
      await loadAll(selectedDeptFilter);
    } catch (err) {
      setError(err.message || 'Failed to create department');
    }
  };

  const submitTemplate = async (e) => {
    e.preventDefault();
    try {
      const parsed = JSON.parse(templateForm.template_json);
      await templatesService.createTemplate(templateForm.department_slug, {
        code: templateForm.code,
        extraction_instructions: templateForm.extraction_instructions || null,
        template_json: parsed,
      });
      setShowTemplateModal(false);
      setTemplateForm({ department_slug: '', code: '', extraction_instructions: '', template_json: EXAMPLE_TEMPLATE_JSON });
      await loadAll(selectedDeptFilter);
    } catch (err) {
      if (err instanceof SyntaxError) setError('Template JSON is not valid JSON.');
      else setError(err.message || 'Failed to create template');
    }
  };

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto p-4 w-full h-full">
      <div className="flex items-center justify-between mb-2">
        <div className="flex gap-2 p-1 bg-bg-secondary rounded-lg border border-border-color w-fit">
          <button
            className={`py-2 px-4 text-sm font-medium rounded-md transition-all duration-200 ${tab === 'departments' ? 'bg-primary-accent text-white shadow-sm' : 'text-secondary'}`}
            style={{ border: 'none', cursor: 'pointer', background: tab === 'departments' ? 'var(--gradient-primary)' : 'transparent', color: tab === 'departments' ? '#fff' : undefined }}
            onClick={() => setTab('departments')}
          >
            <Building2 size={14} style={{ display: 'inline', marginRight: 6 }} /> Departments
          </button>
          <button
            className={`py-2 px-4 text-sm font-medium rounded-md transition-all duration-200 ${tab === 'templates' ? 'bg-primary-accent text-white shadow-sm' : 'text-secondary'}`}
            style={{ border: 'none', cursor: 'pointer', background: tab === 'templates' ? 'var(--gradient-primary)' : 'transparent', color: tab === 'templates' ? '#fff' : undefined }}
            onClick={() => setTab('templates')}
          >
            <Layers size={14} style={{ display: 'inline', marginRight: 6 }} /> Templates
          </button>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary flex items-center gap-2" onClick={() => loadAll(selectedDeptFilter)}>
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
          {tab === 'departments' ? (
            <button className="btn btn-primary flex items-center gap-2" onClick={() => setShowDeptModal(true)}>
              <Plus size={16} /> New Department
            </button>
          ) : (
            <button className="btn btn-primary flex items-center gap-2" onClick={() => setShowTemplateModal(true)}>
              <Plus size={16} /> New Template
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="p-3 bg-danger bg-opacity-10 border border-danger rounded-md text-danger text-sm">{error}</div>
      )}

      {tab === 'templates' && (
        <Card>
          <CardContent>
            <div className="flex items-center gap-3">
              <Building2 size={18} className="text-secondary" />
              <label className="text-sm text-secondary">Filter by department</label>
              <select className="input-field" style={{ maxWidth: '280px' }} value={selectedDeptFilter} onChange={(e) => handleDeptFilterChange(e.target.value)}>
                <option value="">All departments</option>
                {departments.map((d) => <option key={d.slug} value={d.slug}>{d.name || d.slug}</option>)}
              </select>
            </div>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="p-12 flex justify-center"><Loader text="Loading..." /></div>
      ) : tab === 'departments' ? (
        <Card className="flex-1 animate-fade-in-up">
          <CardHeader><CardTitle>Departments</CardTitle></CardHeader>
          <CardContent className="p-0">
            {departments.length === 0 ? (
              <div className="p-8 text-center text-secondary italic">No departments yet.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left border-collapse">
                  <thead className="text-secondary uppercase font-medium" style={{ backgroundColor: 'rgba(0,0,0,0.05)' }}>
                    <tr>
                      <th className="px-4 py-3 border-b border-border-color">Slug</th>
                      <th className="px-4 py-3 border-b border-border-color">Name</th>
                      <th className="px-4 py-3 border-b border-border-color">Description</th>
                      <th className="px-4 py-3 border-b border-border-color">Status</th>
                      <th className="px-4 py-3 border-b border-border-color">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {departments.map((d) => (
                      <tr key={d.slug} className="border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors">
                        <td className="px-4 py-3 font-mono text-xs" style={{ color: d.color || 'var(--primary-accent)' }}>{d.slug}</td>
                        <td className="px-4 py-3 font-semibold text-primary">{d.name}</td>
                        <td className="px-4 py-3 text-secondary">{d.description || '—'}</td>
                        <td className="px-4 py-3">
                          {d.is_active ? (
                            <Badge variant="success" className="flex items-center gap-1 w-fit"><CheckCircle2 size={12} /> Active</Badge>
                          ) : (
                            <Badge variant="default" className="flex items-center gap-1 w-fit"><XCircle size={12} /> Inactive</Badge>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {d.is_active ? (
                            <button
                              className="btn btn-secondary text-xs text-danger"
                              disabled={busyId === d.slug}
                              onClick={() => {
                                if (window.confirm(`Deactivate department "${d.name}"?`)) {
                                  withBusy(d.slug, () => departmentsService.deactivate(d.slug));
                                }
                              }}
                            >
                              Deactivate
                            </button>
                          ) : (
                            <span className="text-xs text-tertiary italic">No reactivate endpoint yet</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card className="flex-1 animate-fade-in-up">
          <CardHeader><CardTitle>Templates</CardTitle></CardHeader>
          <CardContent className="p-0">
            {templates.length === 0 ? (
              <div className="p-8 text-center text-secondary italic">No templates found for this selection.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left border-collapse">
                  <thead className="text-secondary uppercase font-medium" style={{ backgroundColor: 'rgba(0,0,0,0.05)' }}>
                    <tr>
                      <th className="px-4 py-3 border-b border-border-color">Code</th>
                      <th className="px-4 py-3 border-b border-border-color">Department</th>
                      <th className="px-4 py-3 border-b border-border-color">Version</th>
                      <th className="px-4 py-3 border-b border-border-color">Status</th>
                      <th className="px-4 py-3 border-b border-border-color">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {templates.map((t) => (
                      <tr key={t.id} className="border-b border-border-color border-opacity-50 hover:bg-bg-secondary transition-colors">
                        <td className="px-4 py-3 font-semibold text-primary">{t.code}</td>
                        <td className="px-4 py-3 text-secondary">{deptNameById(t.department_id)}</td>
                        <td className="px-4 py-3">v{t.version}</td>
                        <td className="px-4 py-3">
                          {t.is_active ? (
                            <Badge variant="success" className="flex items-center gap-1 w-fit"><CheckCircle2 size={12} /> Active</Badge>
                          ) : (
                            <Badge variant="default" className="flex items-center gap-1 w-fit"><XCircle size={12} /> Inactive</Badge>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            {t.is_active ? (
                              <button className="btn btn-secondary text-xs" disabled={busyId === t.id} onClick={() => withBusy(t.id, () => templatesService.deactivateTemplate(t.id))}>
                                Deactivate
                              </button>
                            ) : (
                              <button className="btn btn-primary text-xs" disabled={busyId === t.id} onClick={() => withBusy(t.id, () => templatesService.activateTemplate(t.id))}>
                                Activate
                              </button>
                            )}
                            <button
                              className="btn btn-secondary text-xs text-danger flex items-center gap-1"
                              disabled={busyId === t.id}
                              onClick={() => {
                                if (window.confirm(`Delete template "${t.code}" v${t.version}? This cannot be undone.`)) {
                                  withBusy(t.id, () => templatesService.deleteTemplate(t.id));
                                }
                              }}
                            >
                              <Trash2 size={12} /> Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {showDeptModal && (
        <Modal title="New Department" onClose={() => setShowDeptModal(false)}>
          <form onSubmit={submitDept} className="document-types-form">
            <div className="input-group">
              <label className="input-label">Slug</label>
              <input className="input-field" required value={deptForm.slug} onChange={(e) => setDeptForm({ ...deptForm, slug: e.target.value })} placeholder="e.g. FINANCE" />
            </div>
            <div className="input-group">
              <label className="input-label">Name</label>
              <input className="input-field" required value={deptForm.name} onChange={(e) => setDeptForm({ ...deptForm, name: e.target.value })} />
            </div>
            <div className="input-group">
              <label className="input-label">Description</label>
              <input className="input-field" value={deptForm.description} onChange={(e) => setDeptForm({ ...deptForm, description: e.target.value })} />
            </div>
            <div className="input-group">
              <label className="input-label">Color</label>
              <input type="color" className="input-field" style={{ height: '42px', padding: '4px' }} value={deptForm.color} onChange={(e) => setDeptForm({ ...deptForm, color: e.target.value })} />
            </div>
            <div className="document-types-form-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setShowDeptModal(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary">Create</button>
            </div>
          </form>
        </Modal>
      )}

      {showTemplateModal && (
        <Modal title="New Template" onClose={() => setShowTemplateModal(false)}>
          <form onSubmit={submitTemplate} className="document-types-form">
            <div className="input-group">
              <label className="input-label">Department</label>
              <select className="input-field" required value={templateForm.department_slug} onChange={(e) => setTemplateForm({ ...templateForm, department_slug: e.target.value })}>
                <option value="" disabled>Select department...</option>
                {departments.filter((d) => d.is_active).map((d) => <option key={d.slug} value={d.slug}>{d.name}</option>)}
              </select>
            </div>
            <div className="input-group">
              <label className="input-label">Code</label>
              <input className="input-field" required value={templateForm.code} onChange={(e) => setTemplateForm({ ...templateForm, code: e.target.value })} placeholder="e.g. INVOICE_V1" />
            </div>
            <div className="input-group">
              <label className="input-label">Extraction instructions (optional)</label>
              <textarea className="input-field" rows={2} value={templateForm.extraction_instructions} onChange={(e) => setTemplateForm({ ...templateForm, extraction_instructions: e.target.value })} />
            </div>
            <div className="input-group">
              <label className="input-label">Template JSON (fields schema)</label>
              <textarea className="input-field" rows={8} style={{ fontFamily: 'monospace', fontSize: '0.8rem' }} value={templateForm.template_json} onChange={(e) => setTemplateForm({ ...templateForm, template_json: e.target.value })} />
            </div>
            <div className="document-types-form-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setShowTemplateModal(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary">Create</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
};

export default ManagePage;
