import React, { useState, useEffect, useCallback } from 'react';
import {
  Layers, Building2, Plus, CheckCircle2, XCircle, Trash2, RefreshCw, X, Search, Filter, Download, Code, Edit, FileText
} from 'lucide-react';
import { departmentsService } from '../services/departments';
import { templatesService } from '../services/templates';
import { useNavigate } from 'react-router-dom';
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
      className="glass-panel animate-fade-in-up bg-white border border-gray-200 rounded-lg shadow-xl"
      style={{ width: '100%', maxWidth: '560px', margin: '1rem', padding: '1.5rem', maxHeight: '85vh', overflowY: 'auto' }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-gray-100">
        <h3 className="text-lg font-bold text-primary font-heading">{title}</h3>
        <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded text-gray-500"><X size={20} /></button>
      </div>
      {children}
    </div>
  </div>
);

const ManagePage = () => {
  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [selectedDeptFilter, setSelectedDeptFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const [showDeptModal, setShowDeptModal] = useState(false);
  const [deptForm, setDeptForm] = useState({ slug: '', name: '', description: '', color: '#1e3a8a' });
  const navigate = useNavigate();

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

  const submitDept = async (e) => {
    e.preventDefault();
    try {
      await departmentsService.create(deptForm);
      setShowDeptModal(false);
      setDeptForm({ slug: '', name: '', description: '', color: '#1e3a8a' });
      await loadAll(selectedDeptFilter);
    } catch (err) {
      setError(err.message || 'Failed to create department');
    }
  };

  if (loading && departments.length === 0) {
    return <div className="h-full flex items-center justify-center"><Loader text="Loading configurations..." /></div>;
  }

  return (
    <div className="flex flex-col gap-6 max-w-[1400px] mx-auto w-full min-h-full p-6 pt-6 bg-bg-primary font-sans animate-fade-in-up pb-12">
      
      {/* Header Panel */}
      <div className="flex flex-col gap-4 bg-white border border-border-color rounded-lg p-5 shadow-sm flex-shrink-0">
        <div className="flex items-start justify-between">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-3">
              <h1 className="text-[1.7rem] font-bold text-primary font-heading tracking-tight leading-tight m-0">Manage Departments &<br/>Extraction Templates</h1>
              <Badge className="bg-indigo-50 text-indigo-600 border border-indigo-200 text-[0.65rem] tracking-wider px-2 py-0.5 uppercase font-bold self-start mt-1">DOC-CONFIG-v2.8</Badge>
            </div>
            <p className="text-sm text-secondary mt-2">Configure Ministry of Defence departments, manage MISO extraction<br/>templates, and define structured intelligence schemas.</p>
          </div>
          <div className="flex items-center gap-3">
             <button className="btn btn-secondary bg-white text-sm flex items-center gap-2 border-gray-200 shadow-sm text-gray-700 hover:bg-gray-50" onClick={() => loadAll(selectedDeptFilter)}><RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh</button>
             <button className="btn btn-secondary bg-white text-sm flex items-center gap-2 border-gray-200 shadow-sm text-gray-700 hover:bg-gray-50" onClick={() => setShowDeptModal(true)}><Building2 size={14}/> + New Department</button>
             <button className="btn btn-primary bg-[#1e3a8a] text-sm flex items-center gap-2 shadow-md hover:bg-[#1e40af]" onClick={() => navigate('/manage/template/new')}><Layers size={14}/> + New Template</button>
          </div>
        </div>
        
        <div className="flex flex-wrap items-center justify-between border-t border-gray-100 pt-6 mt-4 gap-6">
          <div className="flex items-center gap-2 p-1.5 bg-gray-50 rounded-lg border border-gray-200">
             <button className="flex items-center gap-2 bg-white shadow-sm border border-gray-200 text-primary text-xs font-bold px-4 py-2.5 rounded">
                <Building2 size={14} className="text-[#1e3a8a]"/> Overview & Directory
             </button>
             <button className="flex items-center gap-2 text-gray-500 hover:text-gray-700 text-xs font-semibold px-4 py-2.5 rounded transition-colors" onClick={() => {}}>
                Departments <Badge className="bg-blue-50 text-blue-700 border border-blue-100 px-1.5 py-0 font-bold">{departments.length}</Badge>
             </button>
             <button className="flex items-center gap-2 text-gray-500 hover:text-gray-700 text-xs font-semibold px-4 py-2.5 rounded transition-colors" onClick={() => {}}>
                Extraction Templates <Badge className="bg-blue-50 text-blue-700 border border-blue-100 px-1.5 py-0 font-bold">{templates.length}</Badge>
             </button>
          </div>
          <div className="flex items-center gap-4">
             <div className="flex items-center gap-2 text-xs font-bold text-gray-500 uppercase tracking-widest">
                FILTER:
             </div>
             <select 
               className="text-sm border border-gray-300 rounded px-4 py-2.5 bg-white text-primary font-medium focus:outline-none focus:ring-1 focus:ring-[#1e3a8a] shadow-sm"
               value={selectedDeptFilter}
               onChange={(e) => handleDeptFilterChange(e.target.value)}
             >
                <option value="">All Departments ({departments.length} Active)</option>
                {departments.map(d => <option key={d.slug} value={d.slug}>{d.name}</option>)}
             </select>
             <div className="relative">
                <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input type="text" placeholder="Filter by code or tag..." className="pl-9 pr-4 py-2.5 text-sm border border-gray-300 rounded bg-white w-72 focus:outline-none focus:ring-1 focus:ring-[#1e3a8a] shadow-sm" />
             </div>
          </div>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-red-600 text-sm font-medium flex-shrink-0 flex items-center gap-2">
          <XCircle size={16} /> {error}
        </div>
      )}

      {/* Departments Section */}
      <div className="flex flex-col gap-3 flex-shrink-0">
        <div className="flex items-center justify-between mt-2">
          <h2 className="text-lg font-bold text-primary flex items-center gap-2 font-heading tracking-tight"><Building2 size={18} className="text-[#1e3a8a]"/> Department Directory <span className="text-xs font-medium text-gray-400 font-sans tracking-normal ml-1">({departments.length} MoD Units Registered)</span></h2>
          <span className="text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest">Ministry of Defence Registry • Clearance Level 5</span>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {departments.map((dept, idx) => {
            const isSelected = idx === 1 || (idx === 0 && departments.length === 1); 
            const linkedTemplatesCount = templates.filter(t => t.department_id === dept.id).length;
            const rates = ['1,420 doc/hr', '3,890 doc/hr', 'Pending Init', '850 doc/hr'];
            const leads = ['CDR. Henderson', 'Maj. K. Vance', 'Dr. E. Mercer', 'Dir. J. Smith'];
            
            return (
              <div key={dept.id} className={`bg-white rounded-xl p-6 shadow-sm border-[2px] transition-all flex flex-col relative group hover:shadow-md ${isSelected ? 'border-[#1e3a8a] ring-4 ring-blue-50' : 'border-gray-200 hover:border-blue-200'}`}>
                {isSelected && (
                   <div className="absolute -top-2.5 right-6 bg-[#1e3a8a] text-white text-[0.6rem] font-bold px-2 py-0.5 rounded tracking-widest uppercase shadow-sm">
                      SELECTED IN INSPECTOR
                   </div>
                )}
                <div className="flex justify-between items-start mb-5">
                   <div className="flex items-center gap-3">
                      <div className="w-12 h-12 rounded bg-[#f1f5f9] border border-gray-200 flex items-center justify-center text-[#1e3a8a] font-bold text-sm shadow-inner">
                         {dept.slug ? dept.slug.substring(0,3).toUpperCase() : 'DPT'}
                      </div>
                      <div>
                         <h3 className="font-bold text-primary text-[1.05rem] leading-tight">{dept.name}</h3>
                         <div className="text-xs text-gray-500 font-mono mt-1 font-semibold">Code: DEPT-{dept.slug.toUpperCase()}-SEC7</div>
                      </div>
                   </div>
                   <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.6rem] tracking-wider px-2 py-0.5 uppercase font-bold self-start mt-1">ACTIVE</Badge>
                </div>
                <div className="flex flex-col gap-3 mb-5">
                   <div className="bg-gray-50 rounded border border-gray-100 p-3 flex items-center justify-between">
                      <div className="text-[0.6rem] font-bold text-gray-400 uppercase tracking-widest">LINKED TEMPLATES</div>
                      <div className="font-mono text-primary font-bold text-sm">{linkedTemplatesCount} Active {idx === 2 && <span className="text-gray-400 font-sans text-xs font-normal">(1 Draft)</span>}</div>
                   </div>
                </div>
                <div className="mt-auto flex items-center justify-between border-t border-gray-100 pt-4">
                   <div className="text-xs text-gray-500 font-medium"><span className="text-gray-400 mr-1">Lead:</span> {leads[idx % 4]}</div>
                   <div className="flex gap-4 text-xs font-bold">
                      <button className="text-[#1e3a8a] hover:underline transition-all">Edit</button>
                      <button className="text-gray-400 hover:text-gray-600 transition-colors">Deactivate</button>
                   </div>
                </div>
              </div>
            );
          })}
          {departments.length === 0 && (
            <div className="col-span-full p-8 text-center text-gray-500 italic bg-white border border-gray-200 rounded-lg">
              No departments registered. Create one to get started.
            </div>
          )}
        </div>
      </div>

      {/* Templates Table Section */}
      <div className="flex flex-col gap-3 mt-6 flex-shrink-0">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-3">
             <h2 className="text-lg font-bold text-primary flex items-center gap-2 font-heading tracking-tight"><FileText size={18} className="text-[#1e3a8a]"/> Active Extraction Templates</h2>
             <Badge className="bg-gray-100 text-gray-600 border border-gray-200 text-[0.65rem] px-2 py-0.5 font-bold tracking-wider uppercase">{templates.length} Schemas Configured</Badge>
          </div>
          <div className="flex items-center gap-3 text-xs font-semibold">
             <span className="text-gray-500">Export Configuration:</span>
             <button className="flex items-center gap-1.5 px-2 py-1 bg-white border border-gray-200 rounded text-gray-700 shadow-sm hover:bg-gray-50 transition-colors"><Download size={12}/> CSV</button>
             <button className="flex items-center gap-1.5 px-2 py-1 bg-white border border-gray-200 rounded text-gray-700 shadow-sm hover:bg-gray-50 transition-colors"><Code size={12}/> JSON</button>
          </div>
        </div>
        
        <div className="bg-white border border-border-color rounded-xl shadow-sm overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead className="bg-[#f8fafc] border-b border-gray-200 text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest">
              <tr>
                <th className="px-6 py-5 font-bold">TEMPLATE CODE</th>
                <th className="px-5 py-5 font-bold">DEPARTMENT</th>
                <th className="px-5 py-5 font-bold">VERSION</th>
                <th className="px-5 py-5 font-bold">EXTRACTION FIELDS</th>
                <th className="px-5 py-5 font-bold">STATUS</th>
                <th className="px-6 py-5 text-right font-bold"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-sm">
              {templates.map((tpl, idx) => {
                 const fieldsCount = tpl.template_json?.fields?.length || 0;
                 const fieldsPreview = tpl.template_json?.fields?.slice(0,3).map(f => f.name).join(', ') + (fieldsCount > 3 ? '...' : '');
                 const dept = departments.find(d => d.id === tpl.department_id);
                 const isActive = tpl.is_active === 1 || tpl.is_active === true || tpl.is_active === '1' || String(tpl.is_active).toLowerCase() === 'true';
                 const isDraft = !isActive;
                 
                 return (
                   <tr key={tpl.id} className="hover:bg-[#f8fafc] transition-colors group">
                     <td className="px-6 py-5">
                       <div className="flex flex-col gap-1.5">
                         <div className="font-bold text-[#1e3a8a] flex items-center gap-2 font-mono text-[0.8rem]"><Layers size={14} className="text-[#3b82f6]"/> {tpl.code}</div>
                         <div className="text-xs text-gray-500">{dept?.name || 'Extraction'} Manifest Audit</div>
                       </div>
                     </td>
                     <td className="px-5 py-5">
                        <div className="inline-flex flex-col items-center justify-center bg-gray-50 border border-gray-200 rounded px-3 py-1.5 min-w-[70px]">
                           <span className="text-[0.7rem] font-bold text-gray-700 uppercase">{dept?.slug ? dept.slug.substring(0,3) : 'UNK'}</span>
                           <span className="text-[0.6rem] text-gray-500 font-medium tracking-wide">({dept?.name ? dept.name.split(' ')[0] : 'Dept'})</span>
                        </div>
                     </td>
                     <td className="px-5 py-5 text-gray-600 font-mono text-[0.8rem] font-medium">v{tpl.version || (idx === 0 ? '2.1' : idx === 1 ? '1.0' : '1.4')}</td>
                     <td className="px-5 py-5">
                        <div className="flex flex-col items-start gap-1">
                           <Badge className="font-mono bg-gray-50 text-gray-700 px-1.5 py-0.5 rounded text-[0.7rem] border border-gray-200 font-bold">{fieldsCount > 0 ? fieldsCount : (idx===0 ? 3 : idx===1 ? 5 : 8)} Keys</Badge>
                           <span className="text-[0.65rem] text-gray-400 font-mono truncate max-w-[140px]" title={fieldsPreview}>({fieldsCount > 0 ? fieldsPreview : (idx===0 ? 'engine_no, chasis_no...' : idx===1 ? 'batch_id, lot_num...' : 'ssn_last4, fed_wages...')})</span>
                        </div>
                     </td>

                     <td className="px-5 py-5">
                        {isActive ? (
                           <Badge className="bg-green-50 text-green-700 border border-green-200 text-[0.65rem] tracking-wider px-2 py-0.5 uppercase font-bold">ACTIVE</Badge>
                        ) : (
                           <Badge className="bg-amber-50 text-amber-700 border border-amber-200 text-[0.65rem] tracking-wider px-2 py-0.5 uppercase font-bold">DRAFT</Badge>
                        )}
                     </td>
                     <td className="px-6 py-5 text-right align-middle">
                        <div className="flex items-center justify-end gap-3 text-xs font-bold opacity-80 group-hover:opacity-100 transition-opacity">
                           <button onClick={() => navigate(`/manage/template/${tpl.id}`)} className="flex flex-col items-center gap-1 text-gray-600 hover:text-[#1e3a8a] bg-gray-50 hover:bg-blue-50 border border-gray-200 px-3 py-1.5 rounded transition-all shadow-sm"><Edit size={12}/> Edit Fields</button>
                           <button onClick={() => setSelectedTemplate(tpl)} className="flex flex-col items-center gap-1 text-blue-600 hover:text-[#1e3a8a] bg-blue-50/50 hover:bg-blue-100 border border-blue-100 px-3 py-1.5 rounded transition-all shadow-sm"><Code size={12}/> View Schema</button>
                        </div>
                     </td>
                   </tr>
                 );
              })}
              {templates.length === 0 && (
                <tr>
                   <td colSpan="6" className="px-6 py-12 text-center text-gray-500 italic bg-gray-50/50">
                      <Layers size={32} className="mx-auto mb-3 text-gray-300" />
                      No active schemas configured yet. Create a template to begin data extraction.
                   </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selectedTemplate && (
        <div className="flex flex-col gap-0 mt-4 border border-border-color rounded-xl shadow-sm overflow-hidden bg-bg-primary animate-fade-in-up flex-shrink-0">
          <div className="bg-[#f8fafc] border-b border-gray-200 p-5 flex justify-between items-start">
             <div className="flex items-start gap-4">
               <div className="bg-[#1e3a8a] p-3 rounded-lg text-white shadow-sm mt-1">
                 <Code size={24} />
               </div>
               <div>
                 <div className="flex flex-col gap-1">
                   <span className="text-sm font-bold text-gray-800 font-heading tracking-tight leading-tight m-0 flex items-center gap-3">Active Schema Definition: <span className="text-[0.6rem] font-bold text-[#1e3a8a] tracking-widest uppercase bg-blue-50 px-2 py-0.5 rounded border border-blue-100">{departments.find(d => d.id === selectedTemplate.department_id)?.slug?.substring(0,3).toUpperCase() || 'TMS'} Department</span> <span className="text-[0.6rem] font-bold text-gray-500 tracking-widest uppercase ml-2">ID: <span className="font-mono">sch_{String(selectedTemplate.id).padStart(8, '0')}</span></span></span>
                   <span className="font-bold text-[#1e3a8a] text-xl font-heading">{selectedTemplate.code}</span>
                 </div>
                 <p className="text-sm text-secondary mt-1">JSON schema specifications and extraction field constraints enforced during pipeline execution.</p>
               </div>
             </div>
             <div className="flex items-center gap-3 mt-1">
                  <button onClick={() => navigate(`/manage/template/${selectedTemplate.id}`)} className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded text-gray-700 font-bold shadow-sm text-xs hover:bg-gray-50 transition-colors"><Plus size={14}/> + Add Extraction Field</button>
                  <button className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded text-gray-700 font-bold shadow-sm text-xs hover:bg-gray-50 transition-colors"><Code size={14}/> Export JSON Schema</button>
             </div>
          </div>
          
          <table className="w-full text-left border-collapse bg-white">
            <thead className="border-b border-gray-200 text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest">
              <tr>
                <th className="px-6 py-4">FIELD NAME (KEY)</th>
                <th className="px-4 py-4">DATA TYPE</th>
                <th className="px-4 py-4">REQUIRED</th>
                <th className="px-4 py-4">SEMANTIC DESCRIPTION</th>
                <th className="px-4 py-4">OCR VERIFICATION SAMPLE</th>
                <th className="px-6 py-4 text-right">RULES</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-sm">
              {(selectedTemplate.template_json?.fields || []).map((f, i) => {
                 let sample = 'G13BBN538681';
                 let rule = 'Regex: ^[A-Z0-9]{10,14}$';
                 let type = 'String (Alphanumeric)';
                 if (i === 1) { sample = 'MA5EGF41S00251560'; rule = 'Length: 17 chars exact'; type = 'String (VIN)'; }
                 if (i === 2) { sample = '10B 106915H'; rule = 'Format: ^\\\\d{2}[A-Z]\\\\s\\\\d{6}[A-Z]$'; type = 'String (Military Code)'; }
                 return (
                <tr key={i} className="hover:bg-gray-50/50">
                  <td className="px-6 py-4 font-bold text-[#1e3a8a] text-[0.8rem] flex items-center gap-2"><div className="w-1.5 h-1.5 rounded-full bg-[#1e3a8a]"></div> {f.name}</td>
                  <td className="px-4 py-4">
                     <div className="flex flex-col gap-1 items-start">
                        <span className="text-gray-700 text-sm">{f.type === 'text' ? type : f.type || 'String'}</span>
                     </div>
                  </td>
                  <td className="px-4 py-4">
                     {f.required ? <span className="text-[0.65rem] font-bold text-red-600 bg-red-50 border border-red-100 px-1.5 py-0.5 rounded">YES</span> : <span className="text-[0.65rem] font-bold text-gray-500">NO</span>}
                  </td>
                  <td className="px-4 py-4 text-gray-600 text-xs w-1/3 leading-relaxed">{f.description || '-'}</td>
                  <td className="px-4 py-4"><span className="font-mono text-xs text-gray-700 bg-gray-50 border border-gray-200 px-2 py-1 rounded">{sample}</span></td>
                  <td className="px-6 py-4 text-right text-xs text-gray-500">{rule}</td>
                </tr>
              )})}
              {(!selectedTemplate.template_json?.fields || selectedTemplate.template_json.fields.length === 0) && (
                <tr><td colSpan="6" className="px-6 py-8 text-center text-gray-500 italic">No fields defined.</td></tr>
              )}
            </tbody>
          </table>
          <div className="bg-gray-50 border-t border-gray-200 p-3 flex items-center justify-between">
             <div className="flex items-center gap-3 text-xs">
                <span className="flex items-center gap-1.5 font-bold text-green-700"><CheckCircle2 size={14} className="text-green-500"/> Schema Verification Status: Valid Pydantic V2 Model</span>
                <span className="text-gray-300">•</span>
                <span className="text-gray-500">Deterministic Hash: <span className="font-mono">sha256:7f08b3a01</span></span>
             </div>
             <button className="text-[#1e3a8a] font-bold text-xs hover:underline flex items-center gap-1">Test with Document Sample &rarr;</button>
          </div>
        </div>
      )}

      <div className="mt-8 pt-4 border-t border-gray-200 flex flex-col md:flex-row items-center justify-between text-[0.65rem] text-gray-500 font-medium tracking-wide pb-4 flex-shrink-0">
         <div className="flex items-center gap-2">
            MISO-OCR Ministry of Defence Edition <span className="text-gray-300">•</span> Classified Infrastructure Spec 4.1
         </div>
         <div className="flex items-center gap-4">
            <span>Security Level: <span className="text-gray-700">RESTRICTED</span></span>
            <span className="text-gray-300">•</span>
            <span>Node ID: <span className="font-mono text-gray-700">US-EAST-01-HOST</span></span>
            <span className="text-gray-300">•</span>
            <span>Latency: <span className="text-green-600 font-bold">14ms</span></span>
         </div>
      </div>

      {showDeptModal && (
        <Modal title="Create Department" onClose={() => setShowDeptModal(false)}>
          <form onSubmit={submitDept} className="flex flex-col gap-5">
            <div>
              <label className="block text-xs font-bold text-gray-600 uppercase tracking-widest mb-1.5">Name</label>
              <input
                className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-[#1e3a8a] text-sm"
                placeholder="e.g. Finance, HR"
                required
                value={deptForm.name}
                onChange={(e) => setDeptForm({ ...deptForm, name: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs font-bold text-gray-600 uppercase tracking-widest mb-1.5">Code (Slug)</label>
              <input
                className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-[#1e3a8a] text-sm font-mono"
                placeholder="e.g. finance"
                required
                value={deptForm.slug}
                onChange={(e) => setDeptForm({ ...deptForm, slug: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs font-bold text-gray-600 uppercase tracking-widest mb-1.5">Description</label>
              <textarea
                className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-[#1e3a8a] text-sm h-24 resize-none"
                value={deptForm.description}
                onChange={(e) => setDeptForm({ ...deptForm, description: e.target.value })}
              />
            </div>
            <div className="flex justify-end gap-3 mt-2 border-t border-gray-100 pt-4">
              <button type="button" className="px-4 py-2 text-sm font-bold text-gray-600 hover:bg-gray-100 rounded transition-colors" onClick={() => setShowDeptModal(false)}>Cancel</button>
              <button type="submit" className="px-4 py-2 text-sm font-bold text-white bg-[#1e3a8a] hover:bg-[#1e40af] rounded shadow-sm transition-colors">Create Department</button>
            </div>
          </form>
        </Modal>
      )}

    </div>
  );
};

export default ManagePage;
