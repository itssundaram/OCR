import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, File, AlertCircle, Play, Settings } from 'lucide-react';
import { templatesService } from '../services/templates';
import { documentsService } from '../services/documents';
import { departmentsService } from '../services/departments';
import { extractService } from '../services/extract';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card';
import { Loader } from '../components/ui/Loader';

const Upload = () => {
  const navigate = useNavigate();
  
  // Data State
  const [departments, setDepartments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loadingInitial, setLoadingInitial] = useState(true);
  
  // Form State
  const [selectedDept, setSelectedDept] = useState('');
  const [selectedTemplateCode, setSelectedTemplateCode] = useState('');
  const [file, setFile] = useState(null);
  const [isAsync, setIsAsync] = useState(true);
  
  // Drag and Drop State
  const [dragActive, setDragActive] = useState(false);
  
  // Upload State
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  
  const fileInputRef = useRef(null);

  useEffect(() => {
    const fetchInitialData = async () => {
      try {
        const deptRes = await departmentsService.list();
        const activeDepts = deptRes.data.filter(d => d.is_active);
        
        setDepartments(activeDepts);

        if (activeDepts.length > 0) {
          setSelectedDept(activeDepts[0].slug);
        }
      } catch (err) {
        setError('Failed to load form data. ' + err.message);
      } finally {
        setLoadingInitial(false);
      }
    };
    fetchInitialData();
  }, []);

  // Fetch Templates when Department changes
  useEffect(() => {
    if (!selectedDept) {
      setTemplates([]);
      setSelectedTemplateCode('');
      return;
    }
    
    const fetchTemplates = async () => {
      try {
        const res = await templatesService.listTemplates(selectedDept);
        const activeTemplates = res.data.filter(t => t.is_active);
        setTemplates(activeTemplates);
        if (activeTemplates.length > 0) {
          setSelectedTemplateCode(activeTemplates[0].code);
        } else {
          setSelectedTemplateCode('');
        }
      } catch (err) {
        console.error(err);
      }
    };
    fetchTemplates();
  }, [selectedDept]);

  // Drag Events
  const handleDrag = function(e) {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = function(e) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };
  
  const handleFileChange = function(e) {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const onUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file || !selectedTemplateCode) {
      setError("Please fill all required fields and select a file.");
      return;
    }

    setUploading(true);
    setError(null);
    
    try {
      // Always uses extractService.upload which uses BackgroundTasks under the hood on the backend.
      const res = await extractService.upload(selectedDept, selectedTemplateCode, file);
      navigate(`/documents/${res.data.document_id}`);
    } catch (err) {
      setError(err.message || 'Upload failed');
      setUploading(false); // Only reset if failed. If success, we navigate away anyway.
    }
  };

  if (loadingInitial) return <div className="h-full flex items-center justify-center"><Loader text="Loading..." /></div>;

  return (
    <div className="flex flex-col gap-6 max-w-4xl mx-auto h-full p-4">
      <div className="flex flex-col text-center mt-4 mb-6">
        <h2 className="text-3xl font-bold text-primary font-heading mb-2">Process Document</h2>
        <p className="text-secondary max-w-lg mx-auto">
          Upload a scanned image or PDF. Our AI engine will automatically extract the data based on your schema templates.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-6">
        
        <Card className="animate-fade-in border border-primary-accent border-opacity-20 shadow-lg">
          <CardContent className="p-6">
            <div className="grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-lg)' }}>
              
              <div className="input-group mb-0">
                <label className="input-label">Department</label>
                <select 
                  className="input-field" 
                  value={selectedDept} 
                  onChange={(e) => setSelectedDept(e.target.value)}
                  disabled={uploading}
                >
                  <option value="" disabled>Select Department...</option>
                  {departments.map(d => <option key={d.slug} value={d.slug}>{d.name}</option>)}
                </select>
              </div>

              <div className="input-group mb-0">
                <label className="input-label">Template</label>
                <select 
                  className="input-field" 
                  value={selectedTemplateCode} 
                  onChange={(e) => setSelectedTemplateCode(e.target.value)}
                  disabled={!selectedDept || templates.length === 0 || uploading}
                >
                  {templates.length === 0 && <option value="" disabled>No active templates</option>}
                  {templates.map(t => <option key={t.id} value={t.code}>{t.code}</option>)}
                </select>
              </div>
            </div>
            
            <div className="mt-6 flex items-center justify-between p-4 bg-bg-secondary rounded-lg border border-border-color">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-primary-accent bg-opacity-10 text-primary-accent rounded-lg">
                  <Settings size={20} />
                </div>
                <div>
                  <div className="font-medium text-primary">Background Processing</div>
                  <div className="text-xs text-secondary">Queue the extraction job asynchronously (Recommended for large files).</div>
                </div>
              </div>
              <label className="toggle-switch">
                <input 
                  type="checkbox" 
                  checked={isAsync} 
                  onChange={() => setIsAsync(!isAsync)}
                  disabled={uploading}
                />
                <div className="toggle-slider"></div>
              </label>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="p-4 bg-danger bg-opacity-10 border border-danger rounded-lg text-danger flex items-center gap-3 animate-fade-in">
            <AlertCircle size={20} />
            <span>{error}</span>
          </div>
        )}

        <div 
          className={`flex-1 rounded-xl border-2 border-dashed transition-all duration-300 flex flex-col items-center justify-center cursor-pointer min-h-[300px] animate-fade-in
            ${dragActive ? 'border-primary-accent bg-primary-accent bg-opacity-5' : 'border-border-color hover:border-secondary-accent hover:bg-white hover:bg-opacity-5'}
            ${file ? 'bg-primary-accent bg-opacity-5 border-primary-accent' : ''}
          `}
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
          onClick={onUploadClick}
        >
          <input 
            ref={fileInputRef}
            type="file" 
            className="hidden" 
            accept="image/jpeg,image/png,application/pdf" 
            onChange={handleFileChange} 
            disabled={uploading}
          />
          
          {file ? (
            <div className="flex flex-col items-center gap-4 text-center p-6">
              <div className="p-4 bg-primary-accent rounded-full text-white shadow-glow">
                <File size={40} />
              </div>
              <div>
                <div className="text-xl font-bold text-primary">{file.name}</div>
                <div className="text-secondary mt-1">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
              </div>
              <button 
                className="btn btn-secondary mt-2" 
                onClick={(e) => { e.stopPropagation(); setFile(null); }}
                disabled={uploading}
              >
                Choose different file
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 text-center p-6">
              <div className="p-4 bg-bg-secondary rounded-full text-secondary">
                <UploadCloud size={40} />
              </div>
              <div>
                <div className="text-xl font-bold text-primary">Drag & drop your file here</div>
                <div className="text-secondary mt-1">or click to browse from your computer</div>
              </div>
              <div className="text-xs text-tertiary mt-2">Supports PDF, PNG, JPG</div>
            </div>
          )}
        </div>

        <div className="flex justify-center mt-4">
          <button 
            type="submit" 
            className="btn btn-primary w-full max-w-xs flex justify-center py-3 text-lg shadow-glow"
            disabled={!file || !selectedTemplateCode || uploading}
          >
            {uploading ? (
              <span className="flex items-center gap-2"><Loader size={18} /> Processing...</span>
            ) : (
              <span className="flex items-center gap-2"><Play size={18} /> Start Extraction</span>
            )}
          </button>
        </div>
        
      </form>
    </div>
  );
};

export default Upload;
