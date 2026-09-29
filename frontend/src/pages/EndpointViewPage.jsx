/**
 * Step 4: Endpoint View
 * Shows all available API endpoints for the newly created document type.
 */
import React, { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import config from '../config';

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

export default function EndpointViewPage() {
  const navigate = useNavigate();
  const { state } = useLocation();
  const { department, docType } = state || {};

  if (!department || !docType) {
    navigate('/create');
    return null;
  }

  const dept = department.slug.toLowerCase();
  const type = docType.code.toLowerCase();
  const base = `${BASE_URL}/${dept}/${type}`;

  const endpoints = [
    {
      method: 'POST',
      url: `${base}?max_pages=0`,
      description: 'Upload a document and start extraction. Set max_pages > 0 to limit pages processed.',
      curl: `curl -X POST "${base}?max_pages=0" -F "file=@your_document.pdf"`,
      notes: 'Returns a job_id. Poll the status endpoint to get results.',
    },
    {
      method: 'GET',
      url: `${base}/status/{job_id}`,
      description: 'Poll extraction status. Returns structured JSON result when COMPLETED.',
      curl: `curl "${base}/status/YOUR_JOB_ID"`,
      notes: 'Status values: QUEUED → PROCESSING → COMPLETED | FAILED',
    }
  ];

  return (
    <div className="wizard-page">
      <div className="wizard-header">
        <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.8rem', marginBottom: 'var(--spacing-xs)' }}>
          🎉 Template Created!
        </h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--spacing-md)' }}>
          Step 4 of 4 — Your endpoints are live. Copy and use them in your application.
        </p>
        <Stepper step={3} />
      </div>

      {/* Success banner */}
      <div style={{
        width: '100%', maxWidth: 900,
        background: 'linear-gradient(135deg, #f0fdf4, #dcfce7)',
        border: '2px solid rgba(16, 185, 129, 0.3)',
        borderRadius: 'var(--radius-xl)',
        padding: 'var(--spacing-xl)',
        marginBottom: 'var(--spacing-xl)',
        display: 'flex', alignItems: 'center', gap: 'var(--spacing-lg)',
      }}>
        <div style={{ fontSize: '2.5rem' }}>✅</div>
        <div>
          <h2 style={{ margin: 0, color: '#059669', fontSize: '1.1rem' }}>
            {department.name} / {docType.name} is ready
          </h2>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            API path: <code style={{ background: 'rgba(0,0,0,0.05)', padding: '2px 8px', borderRadius: 4 }}>
              /api/v1/{dept}/{type}
            </code>
          </p>
        </div>
      </div>

      {/* Endpoint cards */}
      <div style={{ width: '100%', maxWidth: 900 }}>
        <h2 style={{ fontSize: '1rem', color: 'var(--text-secondary)', marginBottom: 'var(--spacing-md)', fontWeight: 600 }}>
          AVAILABLE ENDPOINTS
        </h2>
        {endpoints.map((ep, i) => (
          <div key={i} className="endpoint-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--spacing-sm)' }}>
              <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--spacing-sm)' }}>
                <span className={`method-badge method-${ep.method}`}>{ep.method}</span>
                <span className="endpoint-url">{ep.url}</span>
              </div>
              <CopyButton text={ep.url} />
            </div>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: 0, marginBottom: ep.notes ? 'var(--spacing-sm)' : 0 }}>
              {ep.description}
            </p>
            {ep.notes && (
              <p style={{ color: 'var(--text-tertiary)', fontSize: '0.75rem', margin: 0, marginBottom: 'var(--spacing-sm)' }}>
                💡 {ep.notes}
              </p>
            )}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 'var(--spacing-sm)', background: '#1e293b', borderRadius: 'var(--radius-md)', padding: 'var(--spacing-sm) var(--spacing-md)' }}>
              <code style={{ color: '#94a3b8', fontSize: '0.8rem', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ep.curl}
              </code>
              <CopyButton text={ep.curl} />
            </div>
          </div>
        ))}

        {/* Navigation buttons */}
        <div style={{ display: 'flex', gap: 'var(--spacing-md)', marginTop: 'var(--spacing-xl)' }}>
          <button className="btn btn-secondary" onClick={() => navigate('/create')} style={{ flex: 1 }}>
            ← Create Another Type
          </button>
          <button className="btn btn-primary" onClick={() => navigate('/browse')} style={{ flex: 1 }}>
            Browse All Templates →
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/')} style={{ flex: 1 }}>
            🏠 Dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
