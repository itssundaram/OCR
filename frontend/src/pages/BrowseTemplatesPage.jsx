/**
 * Browse Templates Page
 * Shows all existing document types grouped by department.
 * User can click into one to see template form and API endpoints.
 */
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { departmentsService } from '../services/departments';
import api from '../services/api';
import { Loader } from '../components/ui/Loader';

const ICON_MAP = {
  'dollar-sign': '💰', 'users': '👥', 'scale': '⚖️', 'truck': '🚚',
  'heart': '🩺', 'folder': '📁', 'file': '📄', 'settings': '⚙️',
};

export default function BrowseTemplatesPage() {
  const navigate = useNavigate();
  const [departments, setDepartments] = useState([]);
  const [docTypes, setDocTypes] = useState({});
  const [loading, setLoading] = useState(true);
  const [selectedDept, setSelectedDept] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const dRes = await departmentsService.list();
        const depts = dRes.data || [];
        setDepartments(depts);

        // Fetch doc types for all departments
        const dtMap = {};
        await Promise.all(depts.map(async (dept) => {
          try {
            const dtRes = await api.get(`/templates?department_slug=${dept.slug}`);
            const uniqueTypes = new Map();
            (dtRes.data || []).forEach(t => {
              // Prefer active template if there are multiple versions
              if (!uniqueTypes.has(t.code) || t.is_active) {
                t.name = t.code;
                t.description = t.extraction_instructions || 'No description provided.';
                uniqueTypes.set(t.code, t);
              }
            });
            dtMap[dept.slug] = Array.from(uniqueTypes.values());
          } catch {
            dtMap[dept.slug] = [];
          }
        }));
        setDocTypes(dtMap);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    fetchAll();
  }, []);

  const filteredDepts = selectedDept
    ? departments.filter(d => d.slug === selectedDept)
    : departments;

  const searchLower = search.toLowerCase();

  return (
    <div className="wizard-page" style={{ alignItems: 'flex-start', paddingTop: 'var(--spacing-xl)' }}>
      <div style={{ width: '100%', maxWidth: 1100 }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--spacing-xl)' }}>
          <div>
            <button onClick={() => navigate('/')} className="btn btn-secondary" style={{ marginBottom: 'var(--spacing-md)' }}>
              ← Dashboard
            </button>
            <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.8rem', marginBottom: 'var(--spacing-xs)' }}>
              Browse Templates
            </h1>
            <p style={{ color: 'var(--text-secondary)' }}>
              All document types and their extraction templates.
            </p>
          </div>
          <button className="btn btn-primary" onClick={() => navigate('/create')}>
            + Create New Type
          </button>
        </div>

        {/* Search + Dept filter */}
        <div style={{ display: 'flex', gap: 'var(--spacing-md)', marginBottom: 'var(--spacing-xl)', flexWrap: 'wrap' }}>
          <input
            className="input-field"
            placeholder="Search document types..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ flex: 1, minWidth: 200, margin: 0 }}
          />
          <div style={{ display: 'flex', gap: 'var(--spacing-sm)', flexWrap: 'wrap' }}>
            <button
              className={`btn ${!selectedDept ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setSelectedDept(null)}
              style={{ fontSize: '0.8rem' }}
            >
              All
            </button>
            {departments.map(d => (
              <button
                key={d.slug}
                className={`btn ${selectedDept === d.slug ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setSelectedDept(d.slug === selectedDept ? null : d.slug)}
                style={{ fontSize: '0.8rem' }}
              >
                {ICON_MAP[d.icon] || '📁'} {d.name}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <Loader />
        ) : (
          <div>
            {filteredDepts.map(dept => {
              const types = (docTypes[dept.slug] || []).filter(dt =>
                !searchLower || dt.name.toLowerCase().includes(searchLower) || dt.code.toLowerCase().includes(searchLower)
              );
              if (searchLower && types.length === 0) return null;

              return (
                <div key={dept.slug} style={{ marginBottom: 'var(--spacing-2xl)' }}>
                  {/* Department header */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-md)', marginBottom: 'var(--spacing-md)' }}>
                    <div style={{
                      width: 40, height: 40, borderRadius: 'var(--radius-lg)',
                      background: `${dept.color}20`, color: dept.color,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '1.2rem',
                    }}>
                      {ICON_MAP[dept.icon] || '📁'}
                    </div>
                    <div>
                      <h2 style={{ margin: 0, fontSize: '1.1rem', fontFamily: 'var(--font-heading)' }}>{dept.name}</h2>
                      <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                        {types.length} document type{types.length !== 1 ? 's' : ''}
                      </p>
                    </div>
                    <div style={{ flex: 1, height: 1, background: 'var(--border-color)' }} />
                  </div>

                  {types.length === 0 ? (
                    <div style={{ color: 'var(--text-tertiary)', fontSize: '0.85rem', padding: 'var(--spacing-lg)', textAlign: 'center', border: '1px dashed var(--border-color)', borderRadius: 'var(--radius-md)' }}>
                      No document types yet.{' '}
                      <span
                        style={{ color: 'var(--primary-accent)', cursor: 'pointer', textDecoration: 'underline' }}
                        onClick={() => navigate('/create', { state: { department: dept } })}
                      >
                        Create one →
                      </span>
                    </div>
                  ) : (
                    <div className="document-types-grid">
                      {types.map(dt => (
                        <div
                          key={dt.id}
                          className="document-types-card glass-panel"
                          style={{ cursor: 'pointer' }}
                          onClick={() => navigate(`/browse/${dept.slug}/${dt.code}`, { state: { department: dept, docType: dt } })}
                        >
                          <div style={{ padding: 'var(--spacing-lg)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                              <div>
                                <p className="document-types-card-title">{dt.name}</p>
                                <p className="document-types-card-code">{dt.code}</p>
                              </div>
                              <span style={{
                                fontSize: '0.7rem', padding: '2px 8px', borderRadius: 'var(--radius-pill)',
                                background: dt.is_active ? 'rgba(16, 185, 129, 0.1)' : 'rgba(100,116,139,0.1)',
                                color: dt.is_active ? 'var(--success)' : 'var(--text-tertiary)',
                                fontWeight: 600,
                              }}>
                                {dt.is_active ? 'Active' : 'Inactive'}
                              </span>
                            </div>
                            {dt.description && (
                              <p className="document-types-card-desc" style={{ marginTop: 'var(--spacing-sm)' }}>
                                {dt.description}
                              </p>
                            )}
                          </div>
                          <div style={{
                            padding: 'var(--spacing-sm) var(--spacing-lg)',
                            borderTop: '1px solid var(--border-color)',
                            display: 'flex', justifyContent: 'space-between',
                            fontSize: '0.75rem', color: 'var(--text-tertiary)',
                          }}>
                            <code style={{ fontFamily: 'monospace' }}>
                              POST /api/v1/{dept.slug.toLowerCase()}/{dt.code.toLowerCase()}
                            </code>
                            <span>→</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
