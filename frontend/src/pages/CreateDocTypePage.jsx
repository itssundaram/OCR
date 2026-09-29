/**
 * Step 2: Create or Select a Document Type within a Department
 */
import React, { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import api from '../services/api';
import { Loader } from '../components/ui/Loader';

const Stepper = ({ step }) => {
  const steps = ['Department', 'Template Info', 'Schema', 'Endpoints'];
  return (
    <div className="wizard-stepper">
      {steps.map((label, i) => {
        const status = i < step ? 'done' : i === step ? 'active' : 'pending';
        return (
          <React.Fragment key={i}>
            {i > 0 && <div className={`wizard-step-line ${i <= step ? 'done' : ''}`} />}
            <div className="wizard-step">
              <div className={`wizard-step-circle ${status}`}>
                {status === 'done' ? '✓' : i + 1}
              </div>
              <span className={`wizard-step-label ${status}`}>{label}</span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};


export default function CreateDocTypePage() {
  const navigate = useNavigate();
  const { state } = useLocation();
  const department = state?.department;

  const [existingTemplates, setExistingTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState('new'); // 'new' | 'existing'

  // Form state
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!department) { navigate('/create'); return; }
    api.get(`/templates?department_slug=${department.slug}`)
      .then(res => {
        // We only want unique template codes here to show existing "Types" of templates
        const templates = res.data || [];
        const uniqueTemplatesMap = new Map();
        templates.forEach(t => {
          if (!uniqueTemplatesMap.has(t.code)) {
            uniqueTemplatesMap.set(t.code, t);
          }
        });
        setExistingTemplates(Array.from(uniqueTemplatesMap.values()));
      })
      .catch(() => setExistingTemplates([]))
      .finally(() => setLoading(false));
  }, [department, navigate]);

  const handleSelectExisting = (template) => {
    navigate('/create/fields', { state: { department, docType: { code: template.code, name: template.code } } });
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    setError(null);
    if (!code.trim() || !name.trim()) {
      setError('Code and name are required.');
      return;
    }
    setSaving(true);
    // Since we don't have a separate Document Type endpoint anymore, 
    // we just pass the info to the next page to create the template there!
    navigate('/create/fields', {
      state: {
        department,
        docType: { code: code.toUpperCase().trim(), name: name.trim(), description: description.trim() }
      }
    });
  };

  if (!department) return null;

  return (
    <div className="wizard-page">
      <div className="wizard-header">
        <button onClick={() => navigate('/create')} className="btn btn-secondary" style={{ marginBottom: 'var(--spacing-lg)' }}>
          ← Back
        </button>
        <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.8rem', marginBottom: 'var(--spacing-xs)' }}>
          Template Information
        </h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--spacing-md)' }}>
          Step 2 of 4 — Create a new template or add schema fields to an existing one in{' '}
          <strong style={{ color: department.color }}>{department.name}</strong>.
        </p>
        <Stepper step={1} />
      </div>

      <div style={{ width: '100%', maxWidth: 900 }}>
        {/* Mode toggle */}
        <div className="response-format-toggle" style={{ marginBottom: 'var(--spacing-xl)' }}>
          <button className={mode === 'new' ? 'active' : ''} onClick={() => setMode('new')}>
            ✨ Create New Template
          </button>
          <button className={mode === 'existing' ? 'active' : ''} onClick={() => setMode('existing')}>
            📂 Use Existing Template Code
          </button>
        </div>

        {mode === 'new' ? (
          <div className="glass-panel" style={{ padding: 'var(--spacing-xl)' }}>
            <h2 style={{ marginBottom: 'var(--spacing-lg)', fontSize: '1.2rem' }}>Create New Template</h2>
            {error && (
              <div className="document-types-error" style={{ marginBottom: 'var(--spacing-md)' }}>{error}</div>
            )}
            <form onSubmit={handleCreate} className="document-types-form">
              <div className="input-group">
                <label className="input-label">Type Code *</label>
                <input
                  className="input-field"
                  placeholder="e.g. INVOICE"
                  value={code}
                  onChange={e => setCode(e.target.value.toUpperCase())}
                  required
                />
                <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                  Used in API URL: /api/v1/extract/{department.slug.toLowerCase()}/{code.toLowerCase() || '{code}'}
                </span>
              </div>
              <div className="input-group">
                <label className="input-label">Display Name *</label>
                <input
                  className="input-field"
                  placeholder="e.g. Sales Invoice"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  required
                />
              </div>
              <div className="input-group">
                <label className="input-label">Description</label>
                <textarea
                  className="input-field"
                  rows={3}
                  placeholder="Optional description of what this document type is for..."
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  style={{ resize: 'vertical' }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button type="submit" className="btn btn-primary" disabled={saving}>
                  {saving ? 'Creating...' : 'Continue to Field Builder →'}
                </button>
              </div>
            </form>
          </div>
        ) : (
          <div>
            {loading ? (
              <Loader />
            ) : existingTemplates.length === 0 ? (
              <div className="document-types-empty">
                <div className="document-types-empty-icon">📭</div>
                <p>No templates in {department.name} yet.</p>
                <button className="btn btn-primary" style={{ marginTop: 'var(--spacing-md)' }} onClick={() => setMode('new')}>
                  Create one now
                </button>
              </div>
            ) : (
              <div className="document-types-grid">
                {existingTemplates.map(dt => (
                  <div
                    key={dt.id}
                    className="document-types-card glass-panel"
                    onClick={() => handleSelectExisting(dt)}
                  >
                    <div className="document-types-card-header" style={{ padding: 'var(--spacing-lg)' }}>
                      <div>
                        <p className="document-types-card-title">{dt.code}</p>
                        <p className="document-types-card-code">Template version {dt.version}</p>
                      </div>
                    </div>
                    {dt.description && (
                      <div className="document-types-card-content" style={{ padding: '0 var(--spacing-lg) var(--spacing-lg)' }}>
                        <p className="document-types-card-desc">{dt.description}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
