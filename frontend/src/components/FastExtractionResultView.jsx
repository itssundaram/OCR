import React, { useState, useRef, useEffect } from 'react';
import {
  ArrowLeft, FileText, CheckCircle2, XCircle, Clock, Zap, Cpu,
  Copy, Check, RefreshCw, ZoomIn, ZoomOut, Maximize, Crosshair,
  ShieldCheck, Eye, Layers, Sparkles, ChevronRight, AlertCircle, Database,
  Edit2, Lock, Unlock, RotateCcw, Save, X
} from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import { Document, Page, pdfjs } from 'react-pdf';
import { Badge } from './ui/Badge';
import { evidenceService } from '../services/evidence';
import { extractService } from '../services/extract';
import { templatesService } from '../services/templates';
import { config } from '../config';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.js';

const parseJSONRecursively = (data) => {
  let parsed = data;
  while (typeof parsed === 'string') {
    try {
      const next = JSON.parse(parsed);
      if (typeof next === 'string' && next === parsed) break;
      parsed = next;
    } catch (e) {
      break;
    }
  }
  return parsed;
};

const FastExtractionResultView = ({
  activeJob,
  onBack,
  pipelineSteps = [],
  setZoomedImage,
  onRerun,
}) => {
  const [activeTab, setActiveTab] = useState('results');
  const [selectedField, setSelectedField] = useState(null);
  const [copiedKey, setCopiedKey] = useState(null);
  const [numPages, setNumPages] = useState(null);
  const [pageDimensions, setPageDimensions] = useState({
    width: 620,
    height: 877,
    originalWidth: 1654,
    originalHeight: 2338
  });
  const fieldRefs = useRef({});

  // Human-In-The-Loop Interactive States
  const [localFields, setLocalFields] = useState({});
  const [reOcrLoading, setReOcrLoading] = useState({});
  const [editingField, setEditingField] = useState(null);
  const [editValue, setEditValue] = useState('');
  const [cropOverrides, setCropOverrides] = useState({});
  const [isGeometryLocked, setIsGeometryLocked] = useState(false);
  const [locking, setLocking] = useState(false);
  const [toast, setToast] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [learnedAnchorsState, setLearnedAnchorsState] = useState(null);
  const [hoveredField, setHoveredField] = useState(null);
  const [showOnlyDetected, setShowOnlyDetected] = useState(true);
  const [showReferenceAnchors, setShowReferenceAnchors] = useState(true);

  // Dynamic zoom & container tracking for seeing full PDF correctly
  const [zoomScale, setZoomScale] = useState(1.0);
  const [containerSize, setContainerSize] = useState({ width: 620, height: 750 });
  const canvasContainerRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const dragStateRef = useRef(null);

  // Measure container dimensions for responsive page fitting
  useEffect(() => {
    if (!canvasContainerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 50 && entry.contentRect.height > 50) {
          setContainerSize({
            width: Math.floor(entry.contentRect.width),
            height: Math.floor(entry.contentRect.height),
          });
        }
      }
    });
    ro.observe(canvasContainerRef.current);
    return () => ro.disconnect();
  }, []);

  // Sync state on job update
  useEffect(() => {
    if (activeJob) {
      const parsed = activeJob.extracted_json ? parseJSONRecursively(activeJob.extracted_json) : null;
      const f = parsed?.fields || {};
      setLocalFields(f);
      const ocrMeta = activeJob.ocr_metadata_json ? parseJSONRecursively(activeJob.ocr_metadata_json) : {};
      const la = ocrMeta?.learned_anchors || parsed?.learned_anchors;
      setLearnedAnchorsState(la || null);
      setIsGeometryLocked(Boolean(la?.is_verified));
    }
  }, [activeJob]);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  if (!activeJob) return null;

  const ocrMetadata = activeJob.ocr_metadata_json ? parseJSONRecursively(activeJob.ocr_metadata_json) : {};
  const fieldKeys = Object.keys(localFields);
  const totalFields = fieldKeys.length;
  const extractedCount = fieldKeys.filter((k) => localFields[k]?.found).length;
  const validFieldsCount = fieldKeys.filter((k) => {
    const d = localFields[k];
    return Boolean(d?.found && d?.value && String(d.value).trim().length > 0);
  }).length;

  const isWarmHit = ocrMetadata?.mode === 'WARM_ANCHOR_HIT' || activeJob.ocr_metadata_json?.includes?.('WARM_ANCHOR_HIT');
  const learnedAnchors = learnedAnchorsState || ocrMetadata?.learned_anchors;

  const modifiedFieldKeys = Object.keys(localFields).filter(
    (k) => localFields[k]?.is_adjusted || localFields[k]?.manually_edited || localFields[k]?.match_strategy === 'MANUAL_RE_OCR'
  );
  const hasUnsavedModifications = modifiedFieldKeys.length > 0;

  // Genuine execution duration
  const latencyStr = (() => {
    if (activeJob.started_at && activeJob.completed_at) {
      const started = new Date(activeJob.started_at);
      const completed = new Date(activeJob.completed_at);
      const diffMs = completed - started;
      if (!isNaN(diffMs)) {
        return diffMs < 1000 ? `${diffMs}ms` : `${(diffMs / 1000).toFixed(2)}s`;
      }
    }
    return '-';
  })();

  const avgConfidence = totalFields > 0
    ? (fieldKeys.reduce((acc, k) => acc + (localFields[k]?.confidence || 0), 0) / totalFields) * 100
    : 0;

  const handleCopy = (key, value) => {
    navigator.clipboard.writeText(String(value));
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleSelectField = (key) => {
    setSelectedField(key);
    if (fieldRefs.current[key]) {
      fieldRefs.current[key].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    // Auto-scroll the PDF container to center the bounding box
    if (scrollContainerRef.current) {
      const data = localFields[key];
      if (data?.bbox) {
        const boxTop = data.bbox.y1 * scaleY;
        const container = scrollContainerRef.current;
        const targetScroll = Math.max(0, boxTop - 80);
        container.scrollTo({ top: targetScroll, behavior: 'smooth' });
      }
    }
  };

  const onPdfPageLoadSuccess = (page) => {
    setPageDimensions((prev) => ({
      ...prev,
      originalWidth: page.originalWidth,
      originalHeight: page.originalHeight,
    }));
  };

  // ── Document & Rendered Dimensions ─────────────────────────────────────
  const docImgWidth =
    activeJob.doc_page_width ||
    ocrMetadata?.doc_page_width ||
    activeJob.page_dimensions?.[0]?.width ||
    pageDimensions.originalWidth ||
    1654;

  const docImgHeight =
    activeJob.doc_page_height ||
    ocrMetadata?.doc_page_height ||
    activeJob.page_dimensions?.[0]?.height ||
    pageDimensions.originalHeight ||
    2338;

  const refWidth = learnedAnchors?.page_width || docImgWidth;
  const refHeight = learnedAnchors?.page_height || docImgHeight;

  const pageAspect =
    pageDimensions.originalWidth > 0
      ? pageDimensions.originalHeight / pageDimensions.originalWidth
      : docImgHeight / docImgWidth;

  // Base width defaults to filling container or 620px
  const baseWidth = Math.max(340, (containerSize.width || 640) - 40);
  const renderedWidth = Math.round(baseWidth * zoomScale);
  const renderedHeight = Math.round(renderedWidth * pageAspect);

  // Exact scaling factors for document bounding boxes matching backend crops
  const scaleX = renderedWidth / docImgWidth;
  const scaleY = renderedHeight / docImgHeight;

  // Scaling factors for template reference ground truth boxes
  const refScaleX = renderedWidth / refWidth;
  const refScaleY = renderedHeight / refHeight;

  // Fit Page Handler: scales document so entire height fits on screen with zero clipping
  const handleFitPage = () => {
    if (containerSize.height > 100 && pageAspect > 0) {
      const targetWidth = (containerSize.height - 40) / pageAspect;
      const newScale = targetWidth / baseWidth;
      setZoomScale(Number(Math.max(0.3, Math.min(2.0, newScale)).toFixed(2)));
    }
  };

  const handleFitWidth = () => {
    setZoomScale(1.0);
  };

  const handleZoomIn = () => {
    setZoomScale((prev) => Number(Math.min(2.5, prev + 0.15).toFixed(2)));
  };

  const handleZoomOut = () => {
    setZoomScale((prev) => Number(Math.max(0.4, prev - 0.15).toFixed(2)));
  };

  // ── Drag & Resize Mechanics ───────────────────────────────────────────
  const startDrag = (e, key, mode, initialBbox) => {
    e.stopPropagation();
    e.preventDefault();
    setSelectedField(key);
    dragStateRef.current = {
      key,
      mode,
      startX: e.clientX,
      startY: e.clientY,
      initialBbox: { ...initialBbox },
    };
    setIsDragging(true);
  };

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!dragStateRef.current) return;
      const { key, mode, startX, startY, initialBbox } = dragStateRef.current;
      const dx = (e.clientX - startX) / scaleX;
      const dy = (e.clientY - startY) / scaleY;

      let { x1, y1, x2, y2 } = initialBbox;
      const minSize = 10;

      if (mode === 'move') {
        const w = x2 - x1;
        const h = y2 - y1;
        x1 = Math.max(0, Math.min(docImgWidth - w, x1 + dx));
        y1 = Math.max(0, Math.min(docImgHeight - h, y1 + dy));
        x2 = x1 + w;
        y2 = y1 + h;
      } else {
        if (mode.includes('w')) {
          x1 = Math.max(0, Math.min(x2 - minSize, initialBbox.x1 + dx));
        }
        if (mode.includes('e')) {
          x2 = Math.min(docImgWidth, Math.max(x1 + minSize, initialBbox.x2 + dx));
        }
        if (mode.includes('n')) {
          y1 = Math.max(0, Math.min(y2 - minSize, initialBbox.y1 + dy));
        }
        if (mode.includes('s')) {
          y2 = Math.min(docImgHeight, Math.max(y1 + minSize, initialBbox.y2 + dy));
        }
      }

      setLocalFields((prev) => ({
        ...prev,
        [key]: {
          ...prev[key],
          bbox: {
            x1: Math.round(x1),
            y1: Math.round(y1),
            x2: Math.round(x2),
            y2: Math.round(y2),
          },
          is_adjusted: true,
        },
      }));
    };

    const handleMouseUp = () => {
      if (dragStateRef.current) {
        dragStateRef.current = null;
        setIsDragging(false);
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [scaleX, scaleY, docImgWidth, docImgHeight]);

  // ── Re-OCR Single Field Action ────────────────────────────────────────
  const handleReOcr = async (key) => {
    const fieldData = localFields[key];
    if (!fieldData?.bbox) return;

    setReOcrLoading((prev) => ({ ...prev, [key]: true }));
    try {
      const res = await extractService.reOcrField(activeJob.job_id, key, fieldData.bbox);
      if (res?.data) {
        const { value, confidence, bbox, image_data } = res.data;
        setLocalFields((prev) => ({
          ...prev,
          [key]: {
            ...prev[key],
            value,
            confidence,
            bbox: bbox || prev[key].bbox,
            found: Boolean(value),
            match_strategy: 'MANUAL_RE_OCR',
            is_verified: true,
            is_adjusted: false,
          },
        }));
        if (image_data) {
          setCropOverrides((prev) => ({ ...prev, [key]: image_data }));
        }
        showToast(`Field "${key.replace(/_/g, ' ')}" re-extracted: "${value}"`);
      }
    } catch (err) {
      showToast(`Re-OCR failed: ${err.message}`, 'error');
    } finally {
      setReOcrLoading((prev) => ({ ...prev, [key]: false }));
    }
  };

  // ── Manual Text Editing Actions ───────────────────────────────────────
  const startEditing = (key, currentValue) => {
    setEditingField(key);
    setEditValue(currentValue !== undefined && currentValue !== null ? String(currentValue) : '');
  };

  const saveEditing = async (key) => {
    const fieldData = localFields[key];
    try {
      await extractService.updateField(activeJob.job_id, key, editValue, fieldData?.bbox);
      setLocalFields((prev) => ({
        ...prev,
        [key]: {
          ...prev[key],
          value: editValue,
          confidence: 1.0,
          found: true,
          manually_edited: true,
          is_verified: true,
        },
      }));
      setEditingField(null);
      showToast(`Field "${key.replace(/_/g, ' ')}" updated to "${editValue}"`);
    } catch (err) {
      showToast(`Failed to update field: ${err.message}`, 'error');
    }
  };

  const cancelEditing = () => {
    setEditingField(null);
    setEditValue('');
  };

  // ── Save / Lock Template Ground Truth ──────────────────────────────────
  const handleLockTemplateAnchors = async () => {
    const targetTemplateId = activeJob.template_id;
    if (!targetTemplateId) {
      showToast('Template ID not associated with this job.', 'error');
      return;
    }

    setLocking(true);
    try {
      const fieldsToLock = {};
      Object.entries(localFields).forEach(([k, d]) => {
        if (d?.bbox && d.bbox.x1 !== undefined) {
          fieldsToLock[k] = {
            bbox: d.bbox,
            expected_label: k.replace(/_/g, ' '),
          };
        }
      });

      await templatesService.lockAnchors(targetTemplateId, fieldsToLock, docImgWidth, docImgHeight);
      setIsGeometryLocked(true);
      setLearnedAnchorsState({
        fields: fieldsToLock,
        page_width: docImgWidth,
        page_height: docImgHeight,
        is_verified: true,
        verified_at: new Date().toISOString(),
      });
      // Clear adjustment flags since they are now committed as locked ground truth
      setLocalFields((prev) => {
        const next = { ...prev };
        Object.keys(next).forEach((k) => {
          next[k] = {
            ...next[k],
            is_adjusted: false,
            manually_edited: false,
            is_verified: true,
          };
        });
        return next;
      });
      showToast('Template ground truth updated & locked! Verified coordinates saved to database.', 'success');
    } catch (err) {
      showToast(`Failed to save ground truth: ${err.message}`, 'error');
    } finally {
      setLocking(false);
    }
  };

  // ── Unlock Template Ground Truth ──────────────────────────────────────
  const handleUnlockTemplateAnchors = async () => {
    const targetTemplateId = activeJob.template_id;
    if (!targetTemplateId) {
      showToast('Template ID not associated with this job.', 'error');
      return;
    }

    setLocking(true);
    try {
      await templatesService.unlockAnchors(targetTemplateId);
      setIsGeometryLocked(false);
      setLearnedAnchorsState((prev) => (prev ? { ...prev, is_verified: false } : prev));
      showToast('Ground truth unlocked! You can now adjust coordinates or re-calibrate anytime.', 'info');
    } catch (err) {
      showToast(`Failed to unlock ground truth: ${err.message}`, 'error');
    } finally {
      setLocking(false);
    }
  };

  // ── Reset Template Anchor Memory ──────────────────────────────────────
  const handleResetTemplateAnchors = async () => {
    const targetTemplateId = activeJob.template_id;
    if (!targetTemplateId) {
      showToast('Template ID not associated with this job.', 'error');
      return;
    }
    if (!window.confirm('Are you sure you want to completely purge stored ground-truth anchor geometry for this template?')) {
      return;
    }

    setLocking(true);
    try {
      await templatesService.resetAnchors(targetTemplateId);
      setIsGeometryLocked(false);
      setLearnedAnchorsState(null);
      showToast('Template geometric memory has been reset.', 'info');
    } catch (err) {
      showToast(`Failed to reset ground truth: ${err.message}`, 'error');
    } finally {
      setLocking(false);
    }
  };

  return (
    <div className="flex flex-col h-full w-full overflow-hidden bg-slate-50 text-slate-800 relative">
      {/* Toast Notification */}
      {toast && (
        <div
          className={`fixed bottom-6 right-6 z-50 px-4 py-2.5 rounded-lg shadow-xl text-xs font-bold flex items-center gap-2 animate-fade-in-up border ${
            toast.type === 'error' ? 'bg-red-900 text-white border-red-700' : 'bg-slate-900 text-white border-slate-700'
          }`}
        >
          {toast.type === 'error' ? <AlertCircle size={15} className="text-red-400" /> : <CheckCircle2 size={15} className="text-emerald-400" />}
          <span>{toast.message}</span>
        </div>
      )}

      {/* Top Banner: Pipeline Status, Verification Lock & True Latency */}
      <div className="flex flex-col gap-3 bg-white border border-slate-200 rounded-xl p-4 mb-4 shadow-sm flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {onBack && (
              <button
                className="p-2 border border-slate-200 rounded-lg hover:bg-slate-100 transition-colors text-slate-600 cursor-pointer"
                onClick={onBack}
                title="Go back"
              >
                <ArrowLeft size={18} />
              </button>
            )}
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-xl font-bold text-slate-900 tracking-tight m-0 font-heading">
                  Learned Geometric Extraction
                </h1>
                {isWarmHit ? (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300 shadow-sm animate-pulse">
                    <Zap size={14} className="text-emerald-600" />
                    WARM ANCHOR HIT ({latencyStr})
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-indigo-100 text-indigo-800 border border-indigo-300 shadow-sm">
                    <Sparkles size={14} className="text-indigo-600" />
                    COLD LEARNED CACHE ({latencyStr})
                  </span>
                )}
                <span className="text-xs font-mono text-slate-400 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
                  JOB #{activeJob.job_id ? activeJob.job_id.substring(0, 8) : 'unknown'}
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                Sub-second geometric anchor matching with autonomous template coordinate memory. Drag bounding boxes to calibrate.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Ground Truth Status & Management Action */}
            {hasUnsavedModifications ? (
              <button
                onClick={handleLockTemplateAnchors}
                disabled={locking}
                className="py-1.5 px-3.5 text-xs flex items-center gap-2 rounded-lg font-bold shadow-md bg-blue-600 hover:bg-blue-700 text-white border border-blue-600 animate-pulse transition-all cursor-pointer"
                title="Save your modified bounding boxes into template ground truth"
              >
                {locking ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
                <span>Save Ground Truth Changes ({modifiedFieldKeys.length})</span>
              </button>
            ) : isGeometryLocked ? (
              <div className="flex items-center gap-1.5">
                <span className="py-1.5 px-3 text-xs flex items-center gap-1.5 rounded-lg font-bold bg-emerald-50 text-emerald-800 border border-emerald-300 shadow-sm">
                  <ShieldCheck size={14} className="text-emerald-600" />
                  <span>Ground Truth Locked</span>
                </span>
                <button
                  onClick={handleUnlockTemplateAnchors}
                  disabled={locking}
                  className="py-1.5 px-2.5 text-xs flex items-center gap-1.5 rounded-lg font-bold bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 shadow-sm transition-all cursor-pointer"
                  title="Unlock ground truth to allow calibration & modification"
                >
                  {locking ? <RefreshCw size={13} className="animate-spin" /> : <Unlock size={13} className="text-slate-500" />}
                  <span>Unlock</span>
                </button>
              </div>
            ) : (
              <button
                onClick={handleLockTemplateAnchors}
                disabled={locking}
                className="py-1.5 px-3.5 text-xs flex items-center gap-2 rounded-lg font-bold shadow-sm bg-[#1e3a8a] hover:bg-[#1e40af] text-white border border-[#1e3a8a] transition-all cursor-pointer"
                title="Approve and lock current geometry as ground truth in template DB"
              >
                {locking ? <RefreshCw size={14} className="animate-spin" /> : <Lock size={14} />}
                <span>Lock Ground Truth</span>
              </button>
            )}

            {onRerun && (
              <button
                className="btn btn-secondary py-1.5 px-3 text-xs flex items-center gap-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 shadow-sm cursor-pointer"
                onClick={onRerun}
              >
                <RefreshCw size={14} /> Re-extract
              </button>
            )}
          </div>
        </div>

        {/* Live Metrics Strip */}
        <div className="border-t border-slate-100 pt-3 grid grid-cols-4 gap-4 text-xs font-mono">
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Template Code</span>
            <span className="text-slate-800 font-semibold truncate">{activeJob.template_code || 'AUTODETECT'}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Extraction Strategy</span>
            <span className="text-blue-700 font-semibold">{isWarmHit ? 'Geometric Anchor Crops' : 'Adaptive VLM Geometry'}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Fields Extracted</span>
            <span className="text-slate-800 font-semibold">{extractedCount} / {totalFields} fields</span>
          </div>
          <div className="flex flex-col">
            <span className="text-slate-400 font-sans text-[0.65rem] uppercase tracking-wider font-bold">Overall Accuracy</span>
            <span className={`font-bold ${avgConfidence >= 80 ? 'text-emerald-600' : 'text-amber-600'}`}>
              {avgConfidence.toFixed(1)}%
            </span>
          </div>
        </div>
      </div>

      {/* Main Two-Column Interactive View */}
      <div className="flex flex-1 gap-5 overflow-hidden min-h-0">
        {/* Left Column: Interactive Document Canvas with Full Vertical Scrolling */}
        <div className="flex-1 flex flex-col bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm max-w-[48%] h-full">
          <div className="flex items-center justify-between p-3 border-b border-slate-200 bg-slate-50 flex-shrink-0">
            <div className="text-xs font-bold text-slate-700 flex items-center gap-2">
              <Crosshair size={16} className="text-blue-600" />
              Document Spatial Geometry & Anchors
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              {/* Zoom Controls: Fit Page, Fit Width, -, + */}
              <div className="flex items-center bg-white border border-slate-200 rounded p-0.5 shadow-2xs">
                <button
                  onClick={handleFitPage}
                  className="px-1.5 py-0.5 text-[10px] font-bold text-slate-600 hover:text-blue-700 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Fit whole page in view"
                >
                  Fit Page
                </button>
                <button
                  onClick={handleFitWidth}
                  className="px-1.5 py-0.5 text-[10px] font-bold text-slate-600 hover:text-blue-700 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Fit to width"
                >
                  Fit Width
                </button>
                <button
                  onClick={handleZoomOut}
                  className="p-1 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Zoom Out"
                >
                  <ZoomOut size={12} />
                </button>
                <span className="text-[10px] font-mono px-1 font-bold text-slate-700">
                  {Math.round(zoomScale * 100)}%
                </span>
                <button
                  onClick={handleZoomIn}
                  className="p-1 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Zoom In"
                >
                  <ZoomIn size={12} />
                </button>
              </div>

              {/* Filter toggle: only display boxes for detected values */}
              <button
                onClick={() => setShowOnlyDetected(!showOnlyDetected)}
                className={`px-2 py-0.5 rounded text-[11px] font-bold border transition-colors cursor-pointer flex items-center gap-1 ${
                  showOnlyDetected
                    ? 'bg-emerald-50 text-emerald-800 border-emerald-300'
                    : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-100'
                }`}
                title={showOnlyDetected ? "Currently showing only bounding boxes for detected values on this PDF" : "Currently showing all fields"}
              >
                <span>{showOnlyDetected ? `✓ Valid Values Only (${validFieldsCount}/${totalFields})` : `All Fields (${totalFields})`}</span>
              </button>

              {/* Reference Anchor toggle */}
              {learnedAnchors && (
                <button
                  onClick={() => setShowReferenceAnchors(!showReferenceAnchors)}
                  className={`px-2 py-0.5 rounded text-[11px] font-bold border transition-colors cursor-pointer flex items-center gap-1 ${
                    showReferenceAnchors
                      ? 'bg-indigo-50 text-indigo-800 border-indigo-300'
                      : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-100'
                  }`}
                  title="Toggle dashed ground truth reference anchors"
                >
                  <span>{showReferenceAnchors ? '📐 Ref Anchors: ON' : '📐 Ref Anchors: OFF'}</span>
                </button>
              )}

              <span className="px-2 py-0.5 bg-white border border-slate-200 rounded font-mono text-[0.7rem]">
                Page 1 of {numPages || 1}
              </span>
            </div>
          </div>

          {/* Fully Scrollable Canvas Container to view entire PDF correctly */}
          <div
            ref={canvasContainerRef}
            className="flex-1 overflow-hidden bg-slate-100 relative flex flex-col min-h-0"
          >
            {activeJob.document_id ? (
              <div
                ref={scrollContainerRef}
                className="flex-1 overflow-y-auto overflow-x-auto p-4 select-none scroll-smooth flex flex-col items-center"
              >
                <div
                  className="relative inline-block shadow-md my-auto bg-white"
                  style={{ width: renderedWidth, minHeight: renderedHeight }}
                >
                  <Document
                    file={`${config.API_BASE_URL}/documents/${activeJob.document_id}/file`}
                    onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                    loading={<div className="p-10 text-center text-xs text-slate-500">Loading document canvas...</div>}
                  >
                    <Page
                      pageNumber={1}
                      width={renderedWidth}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                      onLoadSuccess={onPdfPageLoadSuccess}
                    />
                  </Document>

                  {/* 1. Ground Truth Reference Bounding Boxes (Dashed Template Baseline) */}
                  {showReferenceAnchors &&
                    learnedAnchors?.fields &&
                    Object.entries(learnedAnchors.fields).map(([refKey, refData]) => {
                      if (!refData?.bbox || refData.bbox.x1 === undefined) return null;
                      const isSelected = selectedField === refKey;
                      const isHovered = hoveredField === refKey;
                      const refLeft = refData.bbox.x1 * refScaleX;
                      const refTop = refData.bbox.y1 * refScaleY;
                      const refWidthPx = (refData.bbox.x2 - refData.bbox.x1) * refScaleX;
                      const refHeightPx = (refData.bbox.y2 - refData.bbox.y1) * refScaleY;

                      return (
                        <div
                          key={`ref_${refKey}`}
                          className={`absolute border-2 border-dashed rounded pointer-events-auto transition-all ${
                            isSelected || isHovered
                              ? 'border-indigo-600 bg-indigo-500/15 z-15 shadow-sm'
                              : 'border-indigo-400/60 bg-indigo-500/5 hover:border-indigo-500 hover:bg-indigo-500/10 z-10'
                          }`}
                          style={{
                            left: `${refLeft}px`,
                            top: `${refTop}px`,
                            width: `${Math.max(16, refWidthPx)}px`,
                            height: `${Math.max(12, refHeightPx)}px`,
                          }}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleSelectField(refKey);
                          }}
                          onMouseEnter={() => setHoveredField(refKey)}
                          onMouseLeave={() => setHoveredField(null)}
                          title={`Ground Truth Reference for ${refKey}: (${refData.bbox.x1}, ${refData.bbox.y1})`}
                        >
                          <span className="absolute -top-3.5 right-0 text-[8px] font-mono font-bold px-1 rounded bg-indigo-100 text-indigo-800 border border-indigo-200 pointer-events-none whitespace-nowrap shadow-xs">
                            Ref: {refKey.replace(/_/g, ' ')}
                          </span>
                        </div>
                      );
                    })}

                  {/* 2. Interactive Bounding Box Overlays for Actual Detected Values */}
                  {Object.entries(localFields).map(([key, data]) => {
                    if (!data?.bbox || data.bbox.x1 === undefined) return null;
                    const isSelected = selectedField === key;
                    const isHovered = hoveredField === key;
                    const hasValidValue = Boolean(data.found && data.value && String(data.value).trim().length > 0);

                    // For this specific PDF, only display bounding boxes for fields with detected values (unless user explicitly selects it)
                    if (showOnlyDetected && !hasValidValue && !isSelected) {
                      return null;
                    }

                    const { x1, y1, x2, y2 } = data.bbox;
                    const boxLeft = x1 * scaleX;
                    const boxTop = y1 * scaleY;
                    const boxWidth = (x2 - x1) * scaleX;
                    const boxHeight = (y2 - y1) * scaleY;

                    return (
                      <div
                        key={key}
                        className={`absolute border-2 rounded transition-all ${
                          isSelected
                            ? 'border-blue-600 bg-blue-500/20 ring-2 ring-blue-400/80 z-25 shadow-md cursor-move'
                            : isHovered
                            ? 'border-blue-500 bg-blue-500/20 ring-1 ring-blue-300 z-20 shadow-sm cursor-pointer'
                            : 'border-emerald-500/90 bg-emerald-500/10 hover:border-blue-500 hover:bg-blue-500/20 cursor-pointer z-15'
                        }`}
                        style={{
                          left: `${boxLeft}px`,
                          top: `${boxTop}px`,
                          width: `${Math.max(16, boxWidth)}px`,
                          height: `${Math.max(12, boxHeight)}px`,
                        }}
                        onClick={() => handleSelectField(key)}
                        onMouseEnter={() => setHoveredField(key)}
                        onMouseLeave={() => setHoveredField(null)}
                        onMouseDown={(e) => {
                          if (isSelected) {
                            startDrag(e, key, 'move', data.bbox);
                          }
                        }}
                        title={`${key}: ${data.value || '(Not extracted)'}`}
                      >
                        {/* Field Name Badge */}
                        <span
                          className={`absolute -top-4 left-0 text-[9px] font-mono font-bold px-1.5 py-0.2 rounded shadow-sm whitespace-nowrap uppercase pointer-events-none flex items-center gap-1 ${
                            isSelected ? 'bg-blue-600 text-white' : 'bg-emerald-700 text-white'
                          }`}
                        >
                          <span>{key.replace(/_/g, ' ')}</span>
                          {hasValidValue && <span className="text-[8px] opacity-80">✓</span>}
                        </span>

                        {/* Interactive Resize Handles & Floating Toolbar when Selected */}
                        {isSelected && (
                          <>
                            {/* Floating Action Pill */}
                            <div
                              className="absolute -bottom-8 left-0 flex items-center gap-2 bg-slate-900/90 text-white backdrop-blur text-[10px] font-bold px-2 py-1 rounded shadow-xl z-30 pointer-events-auto whitespace-nowrap"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <span className="font-mono text-blue-300">
                                ({Math.round(data.bbox.x1)}, {Math.round(data.bbox.y1)})
                              </span>
                              <button
                                onClick={() => handleReOcr(key)}
                                disabled={reOcrLoading[key]}
                                className="flex items-center gap-1 bg-blue-600 hover:bg-blue-500 text-white px-2 py-0.5 rounded font-semibold transition-all disabled:opacity-50 cursor-pointer"
                                title="Re-run OCR on this box only"
                              >
                                {reOcrLoading[key] ? <RefreshCw size={10} className="animate-spin" /> : <Zap size={10} />}
                                Re-OCR Box
                              </button>
                            </div>

                            {/* 4 Corner Handles */}
                            <div
                              className="absolute -top-1.5 -left-1.5 w-3 h-3 bg-white border-2 border-blue-600 rounded-xs cursor-nwse-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'nw', data.bbox)}
                            />
                            <div
                              className="absolute -top-1.5 -right-1.5 w-3 h-3 bg-white border-2 border-blue-600 rounded-xs cursor-nesw-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'ne', data.bbox)}
                            />
                            <div
                              className="absolute -bottom-1.5 -left-1.5 w-3 h-3 bg-white border-2 border-blue-600 rounded-xs cursor-nesw-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'sw', data.bbox)}
                            />
                            <div
                              className="absolute -bottom-1.5 -right-1.5 w-3 h-3 bg-white border-2 border-blue-600 rounded-xs cursor-nwse-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'se', data.bbox)}
                            />

                            {/* 4 Edge Handles */}
                            <div
                              className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-2 bg-white border-2 border-blue-600 rounded-xs cursor-ns-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'n', data.bbox)}
                            />
                            <div
                              className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-2 bg-white border-2 border-blue-600 rounded-xs cursor-ns-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 's', data.bbox)}
                            />
                            <div
                              className="absolute top-1/2 -translate-y-1/2 -left-1.5 w-2 h-3 bg-white border-2 border-blue-600 rounded-xs cursor-ew-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'w', data.bbox)}
                            />
                            <div
                              className="absolute top-1/2 -translate-y-1/2 -right-1.5 w-2 h-3 bg-white border-2 border-blue-600 rounded-xs cursor-ew-resize z-20 shadow-xs"
                              onMouseDown={(e) => startDrag(e, key, 'e', data.bbox)}
                            />
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="text-slate-400 text-sm italic m-auto">Document preview unavailable</div>
            )}
          </div>
        </div>

        {/* Right Column: High-Priority Extracted Results & Upright Source Crops (Original Clean UI) */}
        <div className="flex-1 flex flex-col min-w-0 h-full bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
          {/* Tabs Navigation */}
          <div className="flex items-center justify-between border-b border-slate-200 px-4 pt-2 bg-slate-50 flex-shrink-0">
            <div className="flex gap-4">
              <button
                className={`pb-2.5 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'results' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('results')}
              >
                Extracted Fields ({extractedCount})
              </button>
              <button
                className={`pb-2.5 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'geometry' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('geometry')}
              >
                Learned Geometry Memory
              </button>
              <button
                className={`pb-2.5 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'json' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('json')}
              >
                Raw JSON
              </button>
            </div>
            <span className="text-[11px] font-mono text-slate-400 pb-2">
              Showing {extractedCount} verified results
            </span>
          </div>

          {/* Results Tab Content */}
          {activeTab === 'results' && (
            <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-slate-50/50">
              {fieldKeys.map((key) => {
                const data = localFields[key] || {};
                const confPercent = Math.round((data.confidence || 0) * 100);
                const isSelected = selectedField === key;
                const isHovered = hoveredField === key;
                const hasBbox = data.bbox && data.bbox.x1 !== undefined;
                const isEditing = editingField === key;
                const cropSrc = cropOverrides[key] || evidenceService.imageUrl(activeJob.job_id, key);
                const refBbox = learnedAnchors?.fields?.[key]?.bbox;
                const deltaX = refBbox && hasBbox ? Math.round(data.bbox.x1 - refBbox.x1) : null;
                const deltaY = refBbox && hasBbox ? Math.round(data.bbox.y1 - refBbox.y1) : null;

                return (
                  <div
                    key={key}
                    ref={(el) => (fieldRefs.current[key] = el)}
                    className={`rounded-xl border transition-all duration-200 bg-white overflow-hidden shadow-sm ${
                      isSelected
                        ? 'border-blue-500 ring-2 ring-blue-100 shadow-md'
                        : isHovered
                        ? 'border-blue-400 ring-1 ring-blue-50 shadow-sm'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                    onClick={() => setSelectedField(key)}
                    onMouseEnter={() => setHoveredField(key)}
                    onMouseLeave={() => setHoveredField(null)}
                  >
                    {/* Field Header */}
                    <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50 border-b border-slate-100">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold uppercase tracking-wider text-slate-700">
                          {key.replace(/_/g, ' ')}
                        </span>
                        {data.found ? (
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200 font-mono">
                            {data.match_strategy || 'LEARNED_ANCHOR'}
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-red-50 text-red-700 border border-red-200">
                            NOT FOUND
                          </span>
                        )}
                        {data.manually_edited && (
                          <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-purple-50 text-purple-700 border border-purple-200">
                            MANUAL
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-2">
                        {/* Re-OCR Button */}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleReOcr(key);
                          }}
                          disabled={reOcrLoading[key]}
                          className="flex items-center gap-1 text-[11px] font-bold text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 px-2.5 py-1 rounded transition-colors disabled:opacity-50 shadow-xs cursor-pointer"
                          title="Re-run micro-OCR on this field's bounding box"
                        >
                          {reOcrLoading[key] ? <RefreshCw size={11} className="animate-spin" /> : <Zap size={11} />}
                          <span>Re-OCR</span>
                        </button>

                        <span
                          className={`text-xs font-bold font-mono px-2 py-0.5 rounded ${
                            confPercent >= 80
                              ? 'bg-emerald-50 text-emerald-700'
                              : confPercent >= 50
                              ? 'bg-amber-50 text-amber-700'
                              : 'bg-red-50 text-red-700'
                          }`}
                        >
                          {confPercent}% Conf
                        </span>
                      </div>
                    </div>

                    {/* Field Body: Resolved Value & Inline Edit */}
                    <div className="p-3.5 flex flex-col gap-2">
                      {isEditing ? (
                        <div className="flex items-center gap-2 bg-white border-2 border-blue-500 rounded-lg p-2 shadow-sm" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="text"
                            className="font-mono text-sm font-bold text-slate-900 flex-1 outline-none bg-transparent"
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveEditing(key);
                              if (e.key === 'Escape') cancelEditing();
                            }}
                            autoFocus
                            placeholder="Type ground truth value..."
                          />
                          <button
                            onClick={() => saveEditing(key)}
                            className="p-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded transition-colors cursor-pointer"
                            title="Save value (Enter)"
                          >
                            <Check size={14} />
                          </button>
                          <button
                            onClick={cancelEditing}
                            className="p-1.5 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded transition-colors cursor-pointer"
                            title="Cancel (Esc)"
                          >
                            <X size={14} />
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center justify-between bg-slate-50 border border-slate-200 rounded-lg p-2.5 group">
                          <div className="font-mono text-sm font-bold text-slate-900 break-all select-all flex-1">
                            {data.found ? String(data.value) : <span className="text-red-500 italic">Not extracted</span>}
                          </div>
                          <div className="flex items-center gap-1">
                            <button
                              className="p-1.5 hover:bg-white rounded text-slate-400 hover:text-blue-600 transition-colors cursor-pointer"
                              onClick={(e) => {
                                e.stopPropagation();
                                startEditing(key, data.value);
                              }}
                              title="Manually edit field value"
                            >
                              <Edit2 size={13} />
                            </button>
                            <button
                              className="p-1.5 hover:bg-white rounded text-slate-400 hover:text-slate-700 transition-colors cursor-pointer"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleCopy(key, data.value);
                              }}
                              title="Copy to clipboard"
                            >
                              {copiedKey === key ? <Check size={14} className="text-emerald-600" /> : <Copy size={14} />}
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Extraction Source Crop & Spatial Coordinates */}
                      {data.found && (
                        <div className="mt-1 pt-2 border-t border-slate-100 flex items-center justify-between gap-4">
                          <div className="flex items-center gap-3">
                            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400">
                              SOURCE CROP:
                            </span>
                            <div
                              className="bg-slate-100 border border-slate-200 rounded p-1 max-h-12 max-w-[240px] overflow-hidden flex items-center justify-center cursor-pointer hover:border-blue-400 hover:shadow-sm transition-all"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (setZoomedImage) {
                                  setZoomedImage(cropSrc);
                                }
                              }}
                              title="Click to zoom source crop"
                            >
                              <img
                                src={cropSrc}
                                alt={`Crop for ${key}`}
                                className="h-9 object-contain"
                                onError={(e) => {
                                  e.target.style.display = 'none';
                                }}
                              />
                            </div>
                          </div>

                          {hasBbox && (
                            <div className="flex flex-col items-end gap-1">
                              <div className="flex items-center gap-1.5 text-[11px] font-mono text-slate-700 bg-slate-50 px-2.5 py-0.5 rounded border border-slate-200 shadow-2xs">
                                <Crosshair size={12} className="text-emerald-600" />
                                <span className="font-bold text-[9px] text-slate-400 uppercase tracking-wider">CROP:</span>
                                <span>
                                  ({Math.round(data.bbox.x1)}, {Math.round(data.bbox.y1)}) - ({Math.round(data.bbox.x2)}, {Math.round(data.bbox.y2)})
                                </span>
                              </div>
                              {refBbox && (
                                <div className="flex items-center gap-1.5 text-[10px] font-mono text-slate-500 bg-indigo-50/70 px-2 py-0.5 rounded border border-indigo-100">
                                  <span className="font-bold text-indigo-700">Ref:</span>
                                  <span>({refBbox.x1}, {refBbox.y1})</span>
                                  <span className={`font-semibold ${deltaX === 0 && deltaY === 0 ? 'text-emerald-600' : 'text-amber-600'}`}>
                                    (Δx: {deltaX >= 0 ? `+${deltaX}` : deltaX}, Δy: {deltaY >= 0 ? `+${deltaY}` : deltaY})
                                  </span>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Geometry Memory Tab */}
          {activeTab === 'geometry' && (
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-xs text-blue-900">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2 font-bold">
                    <Database size={16} className="text-blue-600" />
                    Autonomous Anchor Geometry Store
                  </div>
                  {isGeometryLocked ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-200">
                      <ShieldCheck size={12} /> Ground Truth Locked
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold bg-amber-100 text-amber-800 border border-amber-200">
                      <AlertCircle size={12} /> Unverified / Dynamic
                    </span>
                  )}
                </div>
                <p className="text-slate-600">
                  These bounding boxes are stored directly in the template definition. Locking them protects against cold-run drift. You can modify boxes anytime and update ground truth.
                </p>

                {/* Direct Action Buttons in Geometry Tab */}
                <div className="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-blue-200/60">
                  {hasUnsavedModifications ? (
                    <button
                      onClick={handleLockTemplateAnchors}
                      disabled={locking}
                      className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded font-bold text-xs flex items-center gap-1.5 shadow-sm transition-all cursor-pointer animate-pulse"
                    >
                      <Save size={13} /> Save Canvas Changes to Ground Truth ({modifiedFieldKeys.length})
                    </button>
                  ) : isGeometryLocked ? (
                    <button
                      onClick={handleUnlockTemplateAnchors}
                      disabled={locking}
                      className="px-3 py-1.5 bg-white hover:bg-blue-50 text-slate-700 border border-slate-200 rounded font-bold text-xs flex items-center gap-1.5 shadow-sm transition-all cursor-pointer"
                    >
                      <Unlock size={13} /> Unlock Ground Truth
                    </button>
                  ) : (
                    <button
                      onClick={handleLockTemplateAnchors}
                      disabled={locking}
                      className="px-3 py-1.5 bg-[#1e3a8a] hover:bg-[#1e40af] text-white rounded font-bold text-xs flex items-center gap-1.5 shadow-sm transition-all cursor-pointer"
                    >
                      <Lock size={13} /> Lock Current Geometry
                    </button>
                  )}

                  {learnedAnchors && (
                    <button
                      onClick={handleResetTemplateAnchors}
                      disabled={locking}
                      className="px-3 py-1.5 bg-white hover:bg-red-50 text-red-600 border border-red-200 rounded font-bold text-xs flex items-center gap-1.5 shadow-sm transition-all cursor-pointer ml-auto"
                      title="Purge all stored coordinates for this template"
                    >
                      <RotateCcw size={13} /> Reset Geometry Memory
                    </button>
                  )}
                </div>
              </div>

              {learnedAnchors ? (
                <div className="space-y-3 font-mono text-xs">
                  <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                    <div className="text-[10px] uppercase font-bold text-slate-400 mb-1">Reference Canvas</div>
                    <div>Width: {learnedAnchors.page_width}px | Height: {learnedAnchors.page_height}px</div>
                  </div>

                  <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                    <div className="text-[10px] uppercase font-bold text-slate-400 mb-2 flex items-center justify-between">
                      <span>Stored Field Bounding Boxes</span>
                      {hasUnsavedModifications && (
                        <span className="text-amber-700 font-sans font-bold text-[10px] bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                          {modifiedFieldKeys.length} field(s) modified on canvas
                        </span>
                      )}
                    </div>
                    <div className="space-y-1.5">
                      {Object.entries(learnedAnchors.fields || {}).map(([fKey, fData]) => {
                        const localField = localFields[fKey];
                        const isModified = localField?.is_adjusted || localField?.manually_edited || localField?.match_strategy === 'MANUAL_RE_OCR';
                        return (
                          <div key={fKey} className={`flex justify-between items-center text-[11px] border-b border-slate-200/50 py-1.5 ${isModified ? 'bg-amber-50/70 px-2 rounded -mx-2' : ''}`}>
                            <div className="flex items-center gap-2">
                              <span className="font-bold text-slate-700">{fKey}:</span>
                              {isModified && (
                                <span className="font-sans text-[9px] font-bold bg-amber-200 text-amber-900 px-1 rounded">
                                  MODIFIED
                                </span>
                              )}
                            </div>
                            <div className="flex flex-col items-end">
                              <span className={isModified ? 'line-through text-slate-400' : 'text-slate-500'}>
                                ({fData.bbox?.x1}, {fData.bbox?.y1}) - ({fData.bbox?.x2}, {fData.bbox?.y2})
                              </span>
                              {isModified && localField?.bbox && (
                                <span className="text-blue-600 font-bold text-[10px]">
                                  &rarr; ({localField.bbox.x1}, {localField.bbox.y1}) - ({localField.bbox.x2}, {localField.bbox.y2})
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="p-8 text-center text-xs text-slate-400 italic">
                  No geometric memory learned yet for this template.
                </div>
              )}
            </div>
          )}

          {/* Raw JSON Tab */}
          {activeTab === 'json' && (
            <div className="flex-1 overflow-auto p-4 bg-slate-900 text-slate-100 text-xs font-mono">
              <JsonView data={localFields} shouldExpandNode={allExpanded} style={darkStyles} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default FastExtractionResultView;
