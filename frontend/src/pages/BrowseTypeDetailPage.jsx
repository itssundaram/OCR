/**
 * Browse Type Detail Page
 * Shows a specific document type's template as a form + available API endpoints.
 */
import React, { useState, useEffect } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import api from '../services/api';
import { Loader } from '../components/ui/Loader';

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button className={`copy-btn ${copied ? 'copied' : ''}`} onClick={handleCopy}>
      {copied ? '✓ Copied!' : '⎘ Copy'}
    </button>
  );
}

const API_HOST = import.meta.env.VITE_API_HOST || 'http://localhost:8000';
const BASE_URL = `${API_HOST}/api/v1`;

export default function BrowseTypeDetailPage() {
  const navigate = useNavigate();
  const { deptSlug, typeCode } = useParams();
  const { state } = useLocation();

  const [department, setDepartment] = useState(state?.department || null);
  const [docType, setDocType] = useState(state?.docType || null);
  const [template, setTemplate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('template'); // 'template' | 'endpoints'

  useEffect(() => {
    const fetchData = async () => {
      try {
        const tmplRes = await api.get(`/templates/${deptSlug?.toUpperCase()}/${typeCode?.toLowerCase()}`);
        const activeTemplate = tmplRes.data;
        if (activeTemplate) {
          setDocType({
            name: activeTemplate.code,
            code: activeTemplate.code,
            description: activeTemplate.extraction_instructions || 'No description provided.',
          });
          setTemplate(activeTemplate);
        }
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [deptSlug, typeCode]);

  const dept = deptSlug?.toLowerCase() || '';
  const type = typeCode?.toLowerCase() || '';
  const base = `${BASE_URL}/${dept}/${type}`;

  const handleActivate = async () => {
    try {
      await api.post(`/templates/${template.id}/activate`);
      setTemplate({ ...template, is_active: true });
    } catch (e) {
      alert('Failed to activate template');
    }
  };

  const handleDeactivate = async () => {
    try {
      await api.post(`/templates/${template.id}/deactivate`);
      setTemplate({ ...template, is_active: false });
    } catch (e) {
      alert('Failed to deactivate template');
    }
  };

  const handleDelete = async () => {
    if (!window.confirm('Are you sure you want to permanently delete this template version?')) return;
    try {
      await api.delete(`/templates/${template.id}`);
      navigate('/browse');
    } catch (e) {
      alert('Failed to delete template');
    }
  };

  const endpoints = [
    { method: 'POST', url: `${base}?max_pages=0`, description: 'Upload document for extraction.', curl: `curl -X POST "${base}?max_pages=0" -F "file=@doc.pdf"` },
    { method: 'GET', url: `${base}/status/{job_id}`, description: 'Poll extraction status.', curl: `curl "${base}/status/YOUR_JOB_ID"` },
    { method: 'GET', url: `${base}/endpoints`, description: 'List all endpoints.', curl: `curl "${base}/endpoints"` },
  ];

  const fields = template?.template_json?.fields || [];

  return (
    <div className="wizard-page" style={{ alignItems: 'flex-start', paddingTop: 'var(--spacing-xl)' }}>
      <div style={{ width: '100%', maxWidth: 1000 }}>
        {/* Back */}
        <button onClick={() => navigate('/browse')} className="btn btn-secondary" style={{ marginBottom: 'var(--spacing-lg)' }}>
          ← Browse
        </button>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--spacing-xl)' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-sm)', marginBottom: 'var(--spacing-xs)' }}>
              <span className="browse-dept-badge" style={{ background: 'rgba(99,102,241,0.1)', color: 'var(--primary-accent)' }}>
                {deptSlug?.toUpperCase()}
              </span>
              <span style={{ color: 'var(--text-tertiary)' }}>/</span>
              <span className="browse-dept-badge" style={{ background: 'rgba(16,185,129,0.1)', color: 'var(--success)' }}>
                {typeCode?.toUpperCase()}
              </span>
            </div>
            <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.8rem', marginBottom: 'var(--spacing-xs)', display: 'flex', alignItems: 'center', gap: 'var(--spacing-md)' }}>
              {docType?.name || typeCode}
              {template && (
                <span className="browse-dept-badge" style={{ 
                  background: template.is_active ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', 
                  color: template.is_active ? 'var(--success)' : 'var(--error)',
                  fontSize: '0.8rem',
                  verticalAlign: 'middle'
                }}>
                  {template.is_active ? 'Active' : 'Inactive'}
                </span>
              )}
            </h1>
            {docType?.description && (
              <p style={{ color: 'var(--text-secondary)', margin: 0 }}>{docType.description}</p>
            )}
          </div>
          <div style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
            {template && !template.is_active && (
              <button className="btn btn-primary" onClick={handleActivate}>
                ✓ Activate
              </button>
            )}
            {template && template.is_active && (
              <button className="btn btn-secondary" onClick={handleDeactivate}>
                ⏸ Deactivate
              </button>
            )}
            <button className="btn btn-secondary" onClick={() => navigate('/create/fields', { state: { department, docType } })}>
              ✏️ Edit Fields
            </button>
            {template && (
              <button className="btn btn-danger" style={{ background: 'var(--error)', color: '#fff', border: 'none' }} onClick={handleDelete}>
                🗑 Delete
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="response-format-toggle" style={{ marginBottom: 'var(--spacing-xl)' }}>
          <button className={activeTab === 'template' ? 'active' : ''} onClick={() => setActiveTab('template')}>
            📋 Template Fields
          </button>
          <button className={activeTab === 'endpoints' ? 'active' : ''} onClick={() => setActiveTab('endpoints')}>
            🔗 API Endpoints
          </button>
        </div>

        {loading ? (
          <Loader />
        ) : activeTab === 'template' ? (
          <div>
            {!template ? (
              <div className="document-types-empty">
                <div className="document-types-empty-icon">📭</div>
                <p>No template saved yet.</p>
                <button className="btn btn-primary" style={{ marginTop: 'var(--spacing-md)' }}
                  onClick={() => navigate('/create/fields', { state: { department, docType } })}>
                  Create Template
                </button>
              </div>
            ) : (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--spacing-md)' }}>
                  <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text-secondary)', margin: 0 }}>
                    {fields.length} FIELD{fields.length !== 1 ? 'S' : ''} DEFINED
                  </h2>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                    v{template.version}
                  </span>
                </div>

                {/* Column headers */}
                <div style={{
                  display: 'grid', gridTemplateColumns: '1fr 120px 2fr 1fr',
                  gap: 'var(--spacing-md)', padding: '0 var(--spacing-lg)',
                  marginBottom: 'var(--spacing-xs)', fontSize: '0.75rem',
                  color: 'var(--text-tertiary)', fontWeight: 600, textTransform: 'uppercase',
                }}>
                  <span>Field Name</span><span>Type</span><span>Description</span><span>Example</span>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
                  {fields.map((field, i) => (
                    <div key={i} className="bg-bg-secondary border border-border-color rounded-md overflow-hidden animate-fade-in">
                      <div style={{
                        display: 'grid', gridTemplateColumns: '1fr 120px 2fr 1fr',
                        gap: 'var(--spacing-md)', padding: 'var(--spacing-md) var(--spacing-lg)',
                        alignItems: 'center',
                      }}>
                        <code style={{ fontFamily: 'monospace', fontWeight: 600 }}>{field.name}</code>
                        <span style={{
                          fontSize: '0.75rem', padding: '2px 8px', borderRadius: 'var(--radius-pill)',
                          background: 'rgba(99,102,241,0.08)', color: 'var(--primary-accent)',
                          fontWeight: 600, display: 'inline-block',
                        }}>{field.type}</span>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>{field.description || '—'}</span>
                        <span style={{ color: 'var(--text-tertiary)', fontFamily: 'monospace', fontSize: '0.8rem' }}>{field.example || '—'}</span>
                      </div>
                      
                      {/* Advanced features row */}
                      {(field.aliases?.length > 0 || field.extraction_hint || field.known_values?.length > 0) && (
                        <div className="px-6 py-3 bg-bg-primary border-t border-border-color border-opacity-50" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--spacing-md)' }}>
                          <div>
                            <span className="text-xs text-tertiary uppercase tracking-wider block mb-1 font-semibold">Aliases</span>
                            <span className="text-sm text-secondary">{field.aliases?.length > 0 ? field.aliases.join(', ') : '—'}</span>
                          </div>
                          <div>
                            <span className="text-xs text-tertiary uppercase tracking-wider block mb-1 font-semibold">Extraction Hint</span>
                            <span className="text-sm font-mono text-secondary break-all">{field.extraction_hint || '—'}</span>
                          </div>
                          <div>
                            <span className="text-xs text-tertiary uppercase tracking-wider block mb-1 font-semibold">Known Values</span>
                            <span className="text-sm text-secondary">{field.known_values?.length > 0 ? field.known_values.join(', ') : '—'}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: 'var(--spacing-lg)' }}>
              Use these endpoints in your application to extract data from documents.
            </p>
            {endpoints.map((ep, i) => (
              <div key={i} className="endpoint-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--spacing-sm)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-sm)', flexWrap: 'wrap' }}>
                    <span className={`method-badge method-${ep.method}`}>{ep.method}</span>
                    <span className="endpoint-url">{ep.url}</span>
                  </div>
                  <CopyButton text={ep.url} />
                </div>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: 0, marginBottom: 'var(--spacing-sm)' }}>
                  {ep.description}
                </p>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#1e293b', borderRadius: 'var(--radius-md)', padding: 'var(--spacing-sm) var(--spacing-md)' }}>
                  <code style={{ color: '#94a3b8', fontSize: '0.8rem', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {ep.curl}
                  </code>
                  <CopyButton text={ep.curl} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
