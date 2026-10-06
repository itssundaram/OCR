/**
 * DOCINT — Orchestration page, upload step.
 * Department + Template + SEVERAL pipelines selected together; all run
 * concurrently and are reconciled on OrchestrationCanvasPage.
 */
import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, File, AlertCircle, GitCompare } from 'lucide-react';
import { templatesService } from '../services/templates';
import { departmentsService } from '../services/departments';
import { extractService } from '../services/extract';
import { pipelinesService } from '../services/pipelines';
import { Card, CardContent } from '../components/ui/Card';
import { Loader } from '../components/ui/Loader';

const OrchestrationPage = () => {
  const navigate = useNavigate();

  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [pipelines, setPipelines] = useState([]);
  const [loadingInitial, setLoadingInitial] = useState(true);

  const [selectedDept, setSelectedDept] = useState('');
  const [selectedTemplateCode, setSelectedTemplateCode] = useState('');
  const [selectedPipelines, setSelectedPipelines] = useState([]);
  const [file, setFile] = useState(null);

  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const fileInputRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const [deptRes, pipelinesRes] = await Promise.all([
          departmentsService.list(),
          pipelinesService.list(),
        ]);
        const activeDepts = deptRes.data.filter((d) => d.is_active);
        setDepartments(activeDepts);
        if (activeDepts.length > 0) setSelectedDept(activeDepts[0].slug);

        const availablePipelines = pipelinesRes.data.pipelines || [];
        setPipelines(availablePipelines);
        setSelectedPipelines(availablePipelines.map((p) => p.name));
      } catch (err) {
        setError('Failed to load form data. ' + err.message);
      } finally {
        setLoadingInitial(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedDept) { setTemplates([]); setSelectedTemplateCode(''); return; }
    templatesService.listTemplates(selectedDept).then((res) => {
      const activeTemplates = res.data.filter((t) => t.is_active);
      setTemplates(activeTemplates);
      setSelectedTemplateCode(activeTemplates.length > 0 ? activeTemplates[0].code : '');
    }).catch((err) => console.error(err));
  }, [selectedDept]);

  const togglePipeline = (name) => {
    setSelectedPipelines((prev) => (prev.includes(name) ? prev.filter((p) => p !== name) : [...prev, name]));
  };

  const handleDrag = (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
    else if (e.type === 'dragleave') setDragActive(false);
  };
  const handleDrop = (e) => {
    e.preventDefault(); e.stopPropagation(); setDragActive(false);
    if (e.dataTransfer.files?.[0]) setFile(e.dataTransfer.files[0]);
  };
  const handleFileChange = (e) => { if (e.target.files?.[0]) setFile(e.target.files[0]); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file || !selectedTemplateCode || selectedPipelines.length < 2) {
      setError('Select a department, template, file, and at least two pipelines to compare.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const res = await extractService.uploadForComparison(selectedDept, selectedTemplateCode, selectedPipelines, file);
      navigate(`/orchestration/canvas/${res.data.job_id}`);
    } catch (err) {
      setError(err.message || 'Upload failed');
      setUploading(false);
    }
  };

  if (loadingInitial) {
    return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;
  }

  return (
    <div className="flex flex-col gap-6 max-w-4xl mx-auto h-full p-4">
      <div className="flex flex-col text-center mt-4 mb-6">
        <h2 className="text-3xl font-bold text-primary font-heading mb-2">Orchestration — Multi-Pipeline Compare</h2>
        <p className="text-secondary max-w-lg mx-auto">Run every selected pipeline on the same document and watch them reconcile field-by-field on a live workflow canvas.</p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-6">
        <Card className="animate-fade-in-up border border-primary-accent border-opacity-20 shadow-lg">
          <CardContent className="p-6">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-lg)' }}>
              <div className="input-group mb-0">
                <label className="input-label">Department</label>
                <select className="input-field" value={selectedDept} onChange={(e) => setSelectedDept(e.target.value)} disabled={uploading}>
                  <option value="" disabled>Select Department...</option>
                  {departments.map((d) => <option key={d.slug} value={d.slug}>{d.name}</option>)}
                </select>
              </div>
              <div className="input-group mb-0">
                <label className="input-label">Template</label>
                <select className="input-field" value={selectedTemplateCode} onChange={(e) => setSelectedTemplateCode(e.target.value)} disabled={!selectedDept || templates.length === 0 || uploading}>
                  {templates.length === 0 && <option value="" disabled>No active templates</option>}
                  {templates.map((t) => <option key={t.id} value={t.code}>{t.code}</option>)}
                </select>
              </div>
            </div>

            <div className="mt-4">
              <label className="input-label">Pipelines to run together ({selectedPipelines.length} selected)</label>
              <div className="flex flex-col gap-2 mt-2">
                {pipelines.map((p) => (
                  <label key={p.name} className="flex items-center gap-3 p-3 bg-bg-secondary rounded-lg border border-border-color cursor-pointer">
                    <input type="checkbox" checked={selectedPipelines.includes(p.name)} onChange={() => togglePipeline(p.name)} disabled={uploading} />
                    <span className="font-medium text-primary">{p.name}</span>
                    {p.requires_gpu && <span className="text-xs text-tertiary">GPU</span>}
                  </label>
                ))}
                {pipelines.length === 0 && <div className="text-xs text-secondary">No pipelines registered.</div>}
              </div>
              {selectedPipelines.length === 1 && <div className="text-xs text-warning mt-2">Select at least two pipelines to compare.</div>}
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="p-4 bg-danger bg-opacity-10 border border-danger rounded-lg text-danger flex items-center gap-3 animate-fade-in-up">
            <AlertCircle size={20} /><span>{error}</span>
          </div>
        )}

        <div
          className={`flex-1 rounded-xl border-2 border-dashed transition-all duration-300 flex flex-col items-center justify-center cursor-pointer min-h-[220px] animate-fade-in-up
            ${dragActive ? 'border-primary-accent bg-primary-accent bg-opacity-5' : 'border-border-color hover:border-secondary-accent hover:bg-white hover:bg-opacity-5'}
            ${file ? 'bg-primary-accent bg-opacity-5 border-primary-accent' : ''}`}
          onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input ref={fileInputRef} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={handleFileChange} disabled={uploading} />
          {file ? (
            <div className="flex flex-col items-center gap-4 text-center p-6">
              <div className="p-4 bg-primary-accent rounded-full text-white shadow-glow"><File size={40} /></div>
              <div>
                <div className="text-xl font-bold text-primary">{file.name}</div>
                <div className="text-secondary mt-1">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
              </div>
              <button className="btn btn-secondary mt-2" onClick={(e) => { e.stopPropagation(); setFile(null); }} disabled={uploading}>Choose different file</button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 text-center p-6">
              <div className="p-4 bg-bg-secondary rounded-full text-secondary"><UploadCloud size={40} /></div>
              <div>
                <div className="text-xl font-bold text-primary">Drag & drop your file here</div>
                <div className="text-secondary mt-1">or click to browse from your computer</div>
              </div>
              <div className="text-xs text-tertiary mt-2">Supports PDF, PNG, JPG</div>
            </div>
          )}
        </div>

        <div className="flex justify-center mt-4">
          <button type="submit" className="btn btn-primary w-full max-w-xs flex justify-center py-3 text-lg shadow-glow" disabled={!file || !selectedTemplateCode || selectedPipelines.length < 2 || uploading}>
            {uploading ? <span className="flex items-center gap-2"><Loader size={18} /> Dispatching...</span> : <span className="flex items-center gap-2"><GitCompare size={18} /> Run & Compare</span>}
          </button>
        </div>
      </form>
    </div>
  );
};

export default OrchestrationPage;
