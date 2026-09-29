/**
 * Step 3: Field Builder
 * User adds fields (name, type, description, example) to define the extraction template.
 */
import React, { useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import api from '../services/api';

const FIELD_TYPES = [
  { value: 'text', label: '📝 Text' },
  { value: 'number', label: '🔢 Number' },
  { value: 'date', label: '📅 Date' },
  { value: 'boolean', label: '✅ Boolean' },
  { value: 'table', label: '📊 Table' },
];

const Stepper = ({ step }) => {
  const steps = ['Department', 'Doc Type', 'Fields', 'Endpoints'];
  return (
    <div className="wizard-stepper">
      {steps.map((label, i) => {
        const status = i < step ? 'done' : i === step ? 'active' : 'pending';
        return (
          <React.Fragment key={i}>
            {i > 0 && <div className={`wizard-step-line ${i <= step ? 'done' : ''}`} />}
            <div className="wizard-step">
              <div className={`wizard-step-circle ${status}`}>{status === 'done' ? '✓' : i + 1}</div>
              <span className={`wizard-step-label ${status}`}>{label}</span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

const emptyField = () => ({
  id: Math.random().toString(36).slice(2),
  name: '',
  type: 'text',
  description: '',
  example: '',
  aliases: '',
  extraction_hint: '',
  known_values: ''
});

function JsonPreview({ fields, docType, department }) {
  const schema = {
    document_type: docType?.code || 'DOC_TYPE',
    department: department?.slug || 'DEPT',
    fields: fields.filter(f => f.name).map(f => ({
      name: f.name,
      type: f.type,
      description: f.description || "",
      example: f.example || undefined,
      aliases: f.aliases ? f.aliases.split(',').map(s => s.trim()).filter(Boolean) : [],
      extraction_hint: f.extraction_hint || undefined,
      known_values: f.known_values ? f.known_values.split(',').map(s => s.trim()).filter(Boolean) : [],
    })),
  };

  const json = JSON.stringify(schema, null, 2);

  // Simple syntax highlight
  const highlighted = json
    .replace(/"([^"]+)":/g, '<span class="json-key">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="json-string">"$1"</span>')
    .replace(/: (\d+)/g, ': <span class="json-number">$1</span>')
    .replace(/: (true|false)/g, ': <span class="json-boolean">$1</span>');

  return (
    <div>
      <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: 'var(--spacing-sm)' }}>
        Template JSON Preview
      </h3>
      <div className="json-preview" dangerouslySetInnerHTML={{ __html: highlighted }} />
    </div>
  );
}

export default function CreateFieldsPage() {
  const navigate = useNavigate();
  const { state } = useLocation();
  const department = state?.department;
  const docType = state?.docType;

  const [fields, setFields] = useState([emptyField()]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  React.useEffect(() => {
    if (!docType?.code) return;
    const fetchExisting = async () => {
      try {
        const res = await api.get(`/templates?department_slug=${department.slug}`);
        const templates = res.data || [];
        // Find existing template by code
        const matching = templates.filter(t => t.code === docType.code);
        const active = matching.find(t => t.is_active) || matching[0];
        
        if (active && active.template_json && active.template_json.fields) {
          const loadedFields = active.template_json.fields.map(f => ({
            id: Math.random().toString(36).slice(2),
            name: f.name || '',
            type: f.type || 'text',
            description: f.description || '',
            example: f.example || '',
            aliases: f.aliases ? f.aliases.join(', ') : '',
            extraction_hint: f.extraction_hint || '',
            known_values: f.known_values ? f.known_values.join(', ') : ''
          }));
          if (loadedFields.length > 0) {
            setFields(loadedFields);
          }
        }
      } catch (err) {
        console.error("Failed to load existing template fields", err);
      }
    };
    fetchExisting();
  }, [docType]);

  if (!department || !docType) {
    navigate('/create');
    return null;
  }

  const updateField = (id, key, value) => {
    setFields(prev => prev.map(f => f.id === id ? { ...f, [key]: value } : f));
  };

  const addField = () => setFields(prev => [...prev, emptyField()]);

  const removeField = (id) => {
    if (fields.length === 1) return;
    setFields(prev => prev.filter(f => f.id !== id));
  };

  const handleSave = async () => {
    const valid = fields.filter(f => f.name.trim());
    if (valid.length === 0) {
      setError('Add at least one field with a name.');
      return;
    }
    setError(null);
    setSaving(true);

    const templateJson = {
      document_type: docType.code,
      department: department.slug,
      fields: valid.map(f => ({
        name: (f.name || '').trim().replace(/\s+/g, '_').toLowerCase(),
        type: f.type || 'text',
        description: (f.description || '').trim() || "",
        example: (f.example || '').trim() || null,
        aliases: (f.aliases || '').trim() ? f.aliases.split(',').map(s => s.trim()).filter(Boolean) : [],
        extraction_hint: (f.extraction_hint || '').trim() || null,
        known_values: (f.known_values || '').trim() ? f.known_values.split(',').map(s => s.trim()).filter(Boolean) : [],
      })),
    };

    try {
      // Determine next version
      let version = 1;
      try {
        const existing = await api.get(`/templates?department_slug=${department.slug}`);
        const templates = existing.data || [];
        const matching = templates.filter(t => t.code === docType.code);
        if (matching.length > 0) {
          version = Math.max(...matching.map(t => t.version || 0)) + 1;
        }
      } catch { }

      const createRes = await api.post(`/templates/${department.slug}`, {
        code: docType.code,
        template_json: templateJson,
      });

      // Activate it immediately
      if (createRes.data && createRes.data.id) {
        await api.post(`/templates/${createRes.data.id}/activate`);
      }

      navigate('/create/endpoints', { state: { department, docType, templateJson } });
    } catch (err) {
      setError(err.message || 'Failed to save template.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="wizard-page" style={{ alignItems: 'flex-start' }}>
      <div className="wizard-header" style={{ maxWidth: 1100, width: '100%' }}>
        <button onClick={() => navigate('/create/doc-type', { state: { department } })} className="btn btn-secondary" style={{ marginBottom: 'var(--spacing-lg)' }}>
          ← Back
        </button>
        <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.8rem', marginBottom: 'var(--spacing-xs)' }}>
          Field Builder
        </h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--spacing-md)' }}>
          Step 3 of 4 — Define the fields to extract from{' '}
          <strong>{department.name}</strong> / <strong>{docType.name}</strong>.
        </p>
        <Stepper step={2} />
      </div>

      <div className="field-builder-container">
        {/* Left: Field Rows */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--spacing-md)' }}>
            <h2 style={{ fontSize: '1rem', fontWeight: 600, margin: 0 }}>
              Fields ({fields.filter(f => f.name).length} defined)
            </h2>
            <button className="btn btn-secondary" onClick={addField} style={{ fontSize: '0.85rem' }}>
              + Add Field
            </button>
          </div>

          {/* Column headers (hidden for new layout, but keep structural label) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
            {fields.map((field, idx) => (
              <div key={field.id} className="bg-bg-primary p-4 rounded-lg border border-border-color shadow-sm animate-fade-in flex flex-col gap-3" style={{ position: 'relative' }}>
                <button
                  style={{
                    position: 'absolute',
                    top: '8px',
                    right: '12px',
                    background: 'none',
                    border: 'none',
                    cursor: fields.length === 1 ? 'not-allowed' : 'pointer',
                    fontSize: '1rem',
                    padding: '4px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--danger-color, #ef4444)'
                  }}
                  className="opacity-50 hover:opacity-100 transition-opacity"
                  onClick={() => removeField(field.id)}
                  title="Remove field"
                  disabled={fields.length === 1}
                >
                  ✖
                </button>
                
                {/* Main Row */}
                <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 2fr 1.5fr', gap: 'var(--spacing-md)' }}>
                  <div>
                    <label className="text-xs text-tertiary uppercase tracking-wider mb-1 block">Field Name</label>
                    <input className="input-field mb-0" placeholder="invoice_number" value={field.name} onChange={e => updateField(field.id, 'name', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-tertiary uppercase tracking-wider mb-1 block">Type</label>
                    <select className="input-field mb-0" value={field.type} onChange={e => updateField(field.id, 'type', e.target.value)}>
                      {FIELD_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-tertiary uppercase tracking-wider mb-1 block">Description</label>
                    <input className="input-field mb-0" placeholder="What is this field?" value={field.description} onChange={e => updateField(field.id, 'description', e.target.value)} />
                  </div>
                  <div>
                    <label className="text-xs text-tertiary uppercase tracking-wider mb-1 block">Example Value</label>
                    <input className="input-field mb-0" placeholder="e.g. INV-001" value={field.example} onChange={e => updateField(field.id, 'example', e.target.value)} />
                  </div>
                </div>

                {/* Advanced Row (Guardrails) */}
                <div style={{ paddingTop: 'var(--spacing-sm)', borderTop: '1px solid var(--border-color)' }}>
                  <div>
                    <label className="text-xs text-secondary mb-1 block">Aliases (comma separated)</label>
                    <input className="input-field mb-0 text-sm" placeholder="e.g. Invoice No, Inv Num" value={field.aliases} onChange={e => updateField(field.id, 'aliases', e.target.value)} />
                  </div>
                </div>
              </div>
            ))}
          </div>

          {error && (
            <div className="document-types-error" style={{ marginTop: 'var(--spacing-md)' }}>{error}</div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--spacing-sm)', marginTop: 'var(--spacing-lg)' }}>
            <button className="btn btn-secondary" onClick={addField}>+ Add Another Field</button>
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save Template & See Endpoints →'}
            </button>
          </div>
        </div>

        {/* Right: JSON Preview */}
        <JsonPreview fields={fields} docType={docType} department={department} />
      </div>
    </div>
  );
}
