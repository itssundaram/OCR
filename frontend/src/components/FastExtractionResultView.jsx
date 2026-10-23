import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
  ArrowLeft, FileText, CheckCircle2, XCircle, Clock, Zap, Cpu,
  Copy, Check, RefreshCw, ZoomIn, ZoomOut, Maximize, Crosshair,
  ShieldCheck, Eye, Layers, Sparkles, ChevronRight, ChevronLeft, AlertCircle, Database,
  Edit2, Lock, Unlock, RotateCcw, Save, X, Trash2, Search, PlusCircle, Table as TableIcon
} from 'lucide-react';
import { JsonView, allExpanded, darkStyles } from 'react-json-view-lite';
import { Document, Page, pdfjs } from 'react-pdf';
import { Badge } from './ui/Badge';
import KeyValuePanel from './KeyValuePanel';
import TableGrid from './TableGrid';
import LiveFieldsPanel from './LiveFieldsPanel';
import { useJobStream } from '../hooks/useJobStream';
import { evidenceService } from '../services/evidence';
import { extractService } from '../services/extract';
import { templatesService } from '../services/templates';
import { processingService } from '../services/processing';
import { config } from '../config';
import { applyCellPatches } from '../utils/tableGrid';
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

const parsePageNumber = (val) => {
  if (val === null || val === undefined) return null;
  const num = Number(val);
  return Number.isFinite(num) && num >= 1 ? Math.floor(num) : null;
};

// Stream field state -> the field shape the saved result uses, so the panels render it unchanged.
const liveToFields = (liveFields) => {
  const out = {};
  Object.values(liveFields || {}).forEach((f) => {
    const failed = f.state === 'failed';
    out[f.name] = {
      value: failed ? '' : (f.value || ''),
      confidence: f.confidence || 0,
      found: !failed && Boolean(f.value),
      page_number: f.page || 1,
      source_page: f.page || 1,
      bbox: f.bbox || null,
      label: f.label || '',
      source: f.source || '',
      state: f.state || '',
      reason: f.reason || '',
      match_strategy: 'live',
      live: true,
    };
  });
  return out;
};

const FastExtractionResultView = ({
  activeJob,
  onBack,
  pipelineSteps = [],
  setZoomedImage,
  onRerun,
}) => {
  const [currentJob, setCurrentJob] = useState(activeJob);
  const [activeTab, setActiveTab] = useState('results');
  const [selectedField, setSelectedField] = useState(null);
  const [copiedKey, setCopiedKey] = useState(null);
  const [numPages, setNumPages] = useState(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [viewMode, setViewMode] = useState('original'); // 'original' | 'cleaned'
  const [pageDimensions, setPageDimensions] = useState({
    width: 620,
    height: 877,
    originalWidth: 1654,
    originalHeight: 2338
  });
  const fieldRefs = useRef({});

  // Human-In-The-Loop Interactive States
  const [localFields, setLocalFields] = useState({});
  const [localTables, setLocalTables] = useState([]);
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
  const [showAllDetections, setShowAllDetections] = useState(true);
  const [tableHighlight, setTableHighlight] = useState(null); // {page, bbox} of a table cell shown on the canvas
  const [detectionSearch, setDetectionSearch] = useState('');

  // Dynamic zoom & container tracking for seeing full PDF correctly
  const [zoomScale, setZoomScale] = useState(1.0);
  const [containerSize, setContainerSize] = useState({ width: 620, height: 750 });
  const canvasContainerRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const dragStateRef = useRef(null);

  // Sync activeJob prop with currentJob
  useEffect(() => {
    setCurrentJob(activeJob);
  }, [activeJob]);

  // Live auto-polling while job is in progress
  useEffect(() => {
    if (!currentJob?.job_id) return;
    const isWorking = currentJob.status === 'PROCESSING' || currentJob.status === 'QUEUED';
    if (!isWorking) return;

    const interval = setInterval(async () => {
      try {
        const res = await processingService.getJob(currentJob.job_id);
        if (res?.data) {
          setCurrentJob(res.data);
          if (res.data.status === 'COMPLETED' || res.data.status === 'WARNING' || res.data.status === 'FAILED') {
            clearInterval(interval);
          }
        }
      } catch (err) {
        console.error('FastExtractionResultView polling error:', err);
      }
    }, 1500);

    return () => clearInterval(interval);
  }, [currentJob?.job_id, currentJob?.status]);

  // fast_ocr only: field-by-field results over SSE while the job runs (polling above is unchanged)
  // The stream stays open after the first result while the server keeps re-reading flagged table cells.
  const storedRefine = useMemo(() => {
    try {
      const m = currentJob?.ocr_metadata_json ? parseJSONRecursively(currentJob.ocr_metadata_json) : null;
      return m?.refine || null;
    } catch {
      return null;
    }
  }, [currentJob]);
  const refineStoredActive = Boolean(storedRefine && ['pending', 'running'].includes(storedRefine.status));
  // The stream is open for as long as the job is running (from the moment the page opens, with replay of any
  // events already sent) and while the background refinement is still re-reading cells.
  const jobRunning = currentJob?.status === 'PROCESSING' || currentJob?.status === 'QUEUED';
  const live = useJobStream(
    currentJob?.job_id,
    (jobRunning || refineStoredActive) && currentJob?.pipeline_mode === 'fast_ocr'
  );
  const refineView = live.refine.status !== 'idle'
    ? live.refine
    : storedRefine
    ? { status: storedRefine.status, improved: storedRefine.improved || 0, remaining: storedRefine.remaining_flagged ?? storedRefine.flagged_at_start ?? 0, queued: 0 }
    : null;
  const livePatchesRef = useRef([]);
  livePatchesRef.current = live.patches;
  const liveFieldsRef = useRef({});
  liveFieldsRef.current = live.fields;

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
    if (currentJob) {
      const parsed = currentJob.extracted_json ? parseJSONRecursively(currentJob.extracted_json) : null;
      const f = parsed?.fields || {};
      // while the job is still running the saved result does not exist yet: show what the stream has so far
      const running = currentJob.status === 'PROCESSING' || currentJob.status === 'QUEUED';
      setLocalFields(running ? { ...liveToFields(liveFieldsRef.current), ...f } : f);
      setLocalTables(applyCellPatches(Array.isArray(parsed?.tables) ? parsed.tables : [], livePatchesRef.current));
      const ocrMeta = currentJob.ocr_metadata_json ? parseJSONRecursively(currentJob.ocr_metadata_json) : {};
      const la = ocrMeta?.learned_anchors || parsed?.learned_anchors;
      setLearnedAnchorsState(la || null);
      setIsGeometryLocked(Boolean(la?.is_verified));
    }
  }, [currentJob]);

  // field-by-field results from the stream fill the fields panel while the job is running
  useEffect(() => {
    if (!jobRunning || Object.keys(live.fields).length === 0) return;
    setLocalFields((prev) => ({ ...prev, ...liveToFields(live.fields) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.fields]);

  // cells the server re-read in the background arrive one by one; fold them into the grid
  useEffect(() => {
    if (live.patches.length === 0) return;
    setLocalTables((prev) => applyCellPatches(prev, live.patches));
  }, [live.patches]);

  // when refinement ends (or the stream asks for it) load the saved result once so the summary is exact
  const refineStatus = live.refine.status;
  useEffect(() => {
    if (!currentJob?.job_id || !(['done', 'failed', 'cancelled', 'skipped'].includes(refineStatus) || live.needsResync)) return;
    processingService.getJob(currentJob.job_id).then((res) => { if (res?.data) setCurrentJob(res.data); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refineStatus, live.needsResync]);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  };

  if (!currentJob) return null;

  const isWorking = currentJob.status === 'PROCESSING' || currentJob.status === 'QUEUED';
  const ocrMetadata = currentJob.ocr_metadata_json ? parseJSONRecursively(currentJob.ocr_metadata_json) : {};
  const allDetections = ocrMetadata?.all_detections || [];
  const parsedEnvelope = currentJob.extracted_json ? parseJSONRecursively(currentJob.extracted_json) : {};
  const extractedTables = localTables;

  // Document-level stats follow the template's own fields; pairs discovered on the
  // page are listed in the panel but only drive the stats when there is no template field.
  const allFieldKeys = Object.keys(localFields);
  const templateFieldKeys = allFieldKeys.filter((k) => localFields[k]?.source !== 'discovered');
  const fieldKeys = templateFieldKeys.length > 0 ? templateFieldKeys : allFieldKeys;
  const totalFields = fieldKeys.length;
  const extractedCount = allFieldKeys.filter((k) => localFields[k]?.found).length;
  const validFieldsCount = fieldKeys.filter((k) => {
    const d = localFields[k];
    return Boolean(d?.found && d?.value && String(d.value).trim().length > 0);
  }).length;

  const isWarmHit = ocrMetadata?.mode === 'WARM_ANCHOR_HIT' || currentJob.ocr_metadata_json?.includes?.('WARM_ANCHOR_HIT');
  const learnedAnchors = learnedAnchorsState || ocrMetadata?.learned_anchors;

  const modifiedFieldKeys = Object.keys(localFields).filter(
    (k) => localFields[k]?.is_adjusted || localFields[k]?.manually_edited || localFields[k]?.match_strategy === 'MANUAL_RE_OCR'
  );
  const hasUnsavedModifications = modifiedFieldKeys.length > 0;

  // Genuine execution duration
  const latencyStr = (() => {
    if (currentJob.started_at && currentJob.completed_at) {
      const started = new Date(currentJob.started_at);
      const completed = new Date(currentJob.completed_at);
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

  const flaggedCount = fieldKeys.filter((k) => {
    const d = localFields[k];
    const conf = d?.confidence || 0;
    return conf < 0.8 || !d?.found || !d?.value || String(d.value).trim().length === 0;
  }).length;
  const verifiedCount = Math.max(0, totalFields - flaggedCount);



  const handleAddDetectionToTemplate = async (det) => {
    const defaultName = (det.text || 'field')
      .toLowerCase()
      .replace(/[^a-z0-9]/g, '_')
      .replace(/_+/g, '_')
      .slice(0, 30);
    const fieldName = window.prompt(`Enter field name for detected text "${det.text}":`, defaultName);
    if (!fieldName || !fieldName.trim()) return;

    const cleanKey = fieldName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
    try {
      await extractService.manageTemplateField(
        currentJob.job_id,
        'add',
        cleanKey,
        det.text,
        det.bbox,
        parsePageNumber(det.page_number) || parsePageNumber(currentPage) || 1,
        true
      );
      setLocalFields((prev) => ({
        ...prev,
        [cleanKey]: {
          value: det.text,
          confidence: det.confidence || 1.0,
          found: true,
          bbox: det.bbox,
          source_page: parsePageNumber(det.page_number) || parsePageNumber(currentPage) || 1,
          match_strategy: 'USER_ADDED',
          is_verified: true,
        },
      }));
      showToast(`Field "${cleanKey}" added to template & saved to DB!`, 'success');
    } catch (err) {
      showToast(`Failed to add field: ${err.message}`, 'error');
    }
  };

  const handleRemoveField = async (key, isDiscovered = false) => {
    const label = localFields[key]?.label || key.replace(/_/g, ' ');
    const question = isDiscovered
      ? `Dismiss "${label}" from this result? (The template is not changed.)`
      : `Are you sure you want to remove field "${label}" from this template?`;
    if (!window.confirm(question)) return;
    try {
      await extractService.manageTemplateField(
        currentJob.job_id,
        'remove',
        key,
        null,
        null,
        1,
        !isDiscovered
      );
      setLocalFields((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      showToast(isDiscovered ? `"${label}" dismissed.` : `Field "${key}" removed from template.`, 'info');
    } catch (err) {
      showToast(`Failed to remove field: ${err.message}`, 'error');
    }
  };

  const handleCopy = (key, value) => {
    navigator.clipboard.writeText(String(value));
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleSelectField = (key) => {
    setSelectedField(key);
    const data = localFields[key];
    const targetPage =
      parsePageNumber(data?.page_number) ||
      parsePageNumber(data?.source_page) ||
      parsePageNumber(learnedAnchors?.fields?.[key]?.page_number) ||
      parsePageNumber(currentPage) ||
      1;
    if (targetPage !== currentPage) {
      setCurrentPage(targetPage);
    }
    if (fieldRefs.current[key]) {
      fieldRefs.current[key].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    // Auto-scroll the PDF container to center the bounding box
    if (scrollContainerRef.current) {
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
    currentJob.doc_page_width ||
    ocrMetadata?.doc_page_width ||
    currentJob.page_dimensions?.[0]?.width ||
    pageDimensions.originalWidth ||
    1654;

  const docImgHeight =
    currentJob.doc_page_height ||
    ocrMetadata?.doc_page_height ||
    currentJob.page_dimensions?.[0]?.height ||
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
      const fieldPage = parsePageNumber(fieldData.page_number) || parsePageNumber(fieldData.source_page) || parsePageNumber(currentPage) || 1;
      const res = await extractService.reOcrField(currentJob.job_id, key, fieldData.bbox, fieldPage);
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

  // ── The same field read on other pages ─────────────────────────────────
  const handleShowOccurrence = (key, occ) => {
    const target = parsePageNumber(occ?.page_number) || 1;
    setSelectedField(key);
    setCurrentPage(target);
    setTimeout(() => {
      if (scrollContainerRef.current && occ?.bbox) {
        scrollContainerRef.current.scrollTo({ top: Math.max(0, occ.bbox.y1 * scaleY - 120), behavior: 'smooth' });
      }
    }, 150);
  };

  const handleUseOccurrence = async (key, occ) => {
    const cur = localFields[key];
    if (!cur || !occ) return;
    const curPage = parsePageNumber(cur.page_number) || parsePageNumber(cur.source_page) || 1;
    // the reading being replaced stays available as an alternative
    const others = (cur.occurrences || []).filter((o) => o !== occ);
    if (cur.value) {
      others.push({ page_number: curPage, value: cur.value, confidence: cur.confidence, bbox: cur.bbox, source: cur.source || 'previous' });
    }
    try {
      await extractService.updateField(currentJob.job_id, key, occ.value, occ.bbox, {
        page_number: occ.page_number,
        occurrences: others,
      });
      setLocalFields((prev) => ({
        ...prev,
        [key]: {
          ...prev[key],
          value: occ.value,
          bbox: occ.bbox,
          page_number: occ.page_number,
          source_page: occ.page_number,
          confidence: 1.0,
          found: true,
          manually_edited: true,
          is_verified: true,
          occurrences: others,
        },
      }));
      setCurrentPage(parsePageNumber(occ.page_number) || 1);
      showToast(`"${key.replace(/_/g, ' ')}" now uses the page ${occ.page_number} reading`);
    } catch (err) {
      showToast(`Failed to switch reading: ${err.message}`, 'error');
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
      await extractService.updateField(currentJob.job_id, key, editValue, fieldData?.bbox);
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

  const handleLocateCell = ({ page, bbox }) => {
    const target = parsePageNumber(page) || 1;
    setCurrentPage(target);
    setTableHighlight({ page: target, bbox });
    setTimeout(() => {
      if (scrollContainerRef.current && bbox) {
        scrollContainerRef.current.scrollTo({ top: Math.max(0, bbox.y1 * scaleY - 120), behavior: 'smooth' });
      }
    }, 200);
    setTimeout(() => setTableHighlight(null), 8000);
  };

  const handleTableUpdated = (tableIndex, updatedTable) => {
    setLocalTables((prev) => prev.map((t) => (t.table_index === tableIndex ? updatedTable : t)));
  };

  // ── Save / Lock Template Ground Truth ──────────────────────────────────
  const handleLockTemplateAnchors = async () => {
    const targetTemplateId = currentJob.template_id;
    if (!targetTemplateId) {
      showToast('Template ID not associated with this job.', 'error');
      return;
    }

    setLocking(true);
    try {
      const fieldsToLock = {};
      Object.entries(localFields).forEach(([k, d]) => {
        if (d?.source === 'discovered') return; // job-only pairs are not template anchors
        // an empty or failed field has no confirmed value: its box would teach the template a wrong place
        if (!String(d?.value ?? '').trim()) return;
        if (d?.bbox && d.bbox.x1 !== undefined) {
          fieldsToLock[k] = {
            bbox: d.bbox,
            page_number: parsePageNumber(d.source_page) || parsePageNumber(d.page_number) || 1,
            value: d.value ?? null,
            expected_label: k.replace(/_/g, ' '),
          };
        }
      });

      await templatesService.lockAnchors(targetTemplateId, fieldsToLock, docImgWidth, docImgHeight, currentJob.job_id);
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
    const targetTemplateId = currentJob.template_id;
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
    const targetTemplateId = currentJob.template_id;
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

      {/* Top Breadcrumb & Document Review Header (Inspired by Image 4) */}
      <div className="bg-white border border-slate-200 rounded-xl p-3.5 mb-3 shadow-xs flex flex-col gap-2 flex-shrink-0">
        {/* Breadcrumb path */}
        <div className="flex items-center gap-2 text-xs text-slate-500 font-medium">
          {onBack ? (
            <button
              onClick={onBack}
              className="hover:text-blue-700 transition-colors flex items-center gap-1 cursor-pointer font-semibold text-slate-600"
            >
              <ArrowLeft size={13} /> Review queue
            </button>
          ) : (
            <span className="flex items-center gap-1 font-semibold text-slate-600">
              Review queue
            </span>
          )}
          <span className="text-slate-300">/</span>
          <span className="text-slate-800 font-semibold truncate max-w-[380px]">
            {currentJob.original_filename || currentJob.filename || `Job_${(currentJob.job_id || '').substring(0, 8)}`}
          </span>
        </div>

        {/* Document Review Title & Meta Row */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-0.5">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-lg font-bold text-slate-900 tracking-tight font-sans m-0">
              {currentJob.original_filename || currentJob.filename || 'Document'}
            </h1>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
              USM-{(currentJob.document_id || currentJob.job_id || '2026-000020').substring(0, 8).toUpperCase()}
            </span>

            {/* Verification Status Badge */}
            {isWorking ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200 font-mono animate-pulse">
                <RefreshCw size={12} className="animate-spin" /> {currentJob.status === 'QUEUED' && live.status === 'idle' ? 'Queued - waiting for a worker' : 'In Progress'}
              </span>
            ) : flaggedCount > 0 ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-300">
                <AlertCircle size={12} className="text-amber-600" /> Needs review
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-xs font-bold px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-300">
                <CheckCircle2 size={12} className="text-emerald-600" /> Verified
              </span>
            )}

            {/* Document Type Selector Pill */}
            <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-0.5 text-xs font-medium text-slate-700">
              <span className="text-slate-400 font-bold uppercase text-[9px]">Document type:</span>
              <span className="font-semibold text-blue-800 font-mono text-[11px]">
                {currentJob.template_code || 'TMS engine chasis no'}
              </span>
            </div>

            {/* Template Version Pill */}
            <span className="text-[11px] text-slate-500 font-mono bg-slate-100 px-2 py-0.5 rounded border border-slate-200/60">
              tms_family v1.0.0
            </span>

            {/* Flagged Count Pill */}
            {flaggedCount > 0 && (
              <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-amber-100/90 text-amber-800 border border-amber-200 flex items-center gap-1">
                ⚠️ {flaggedCount} flagged
              </span>
            )}

            <span className="text-[11px] text-slate-400 font-mono">↻ run #1</span>
          </div>

          {/* Action Buttons on Right */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-medium text-slate-600 bg-slate-100 px-2.5 py-1 rounded border border-slate-200">
              Run #1 (current) ▾
            </span>

            {/* Ground Truth Status & Management Action */}
            {hasUnsavedModifications ? (
              <button
                onClick={handleLockTemplateAnchors}
                disabled={locking}
                className="py-1 px-3 text-xs flex items-center gap-1.5 rounded-lg font-bold shadow-sm bg-blue-600 hover:bg-blue-700 text-white border border-blue-600 animate-pulse transition-all cursor-pointer"
                title="Save your modified bounding boxes into template ground truth"
              >
                {locking ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />}
                <span>Save Changes ({modifiedFieldKeys.length})</span>
              </button>
            ) : isGeometryLocked ? (
              <div className="flex items-center gap-1.5">
                <span className="py-1 px-2.5 text-xs flex items-center gap-1.5 rounded-lg font-bold bg-emerald-50 text-emerald-800 border border-emerald-300 shadow-2xs">
                  <ShieldCheck size={13} className="text-emerald-600" />
                  <span>Ground Truth Locked</span>
                </span>
                <button
                  onClick={handleUnlockTemplateAnchors}
                  disabled={locking}
                  className="py-1 px-2 text-xs flex items-center gap-1 rounded-lg font-semibold bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 shadow-2xs transition-all cursor-pointer"
                  title="Unlock ground truth to allow calibration & modification"
                >
                  {locking ? <RefreshCw size={12} className="animate-spin" /> : <Unlock size={12} className="text-slate-500" />}
                  <span>Unlock</span>
                </button>
              </div>
            ) : (
              <button
                onClick={handleLockTemplateAnchors}
                disabled={locking}
                className="py-1 px-3 text-xs flex items-center gap-1.5 rounded-lg font-bold shadow-2xs bg-[#1e3a8a] hover:bg-[#1e40af] text-white border border-[#1e3a8a] transition-all cursor-pointer"
                title="Approve and lock current geometry as ground truth in template DB"
              >
                {locking ? <RefreshCw size={12} className="animate-spin" /> : <Lock size={12} />}
                <span>Lock Ground Truth</span>
              </button>
            )}

            {onRerun && (
              <button
                className="py-1 px-2.5 text-xs flex items-center gap-1.5 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 shadow-2xs cursor-pointer rounded-lg font-medium"
                onClick={onRerun}
              >
                <RefreshCw size={12} /> Re-run
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Main Two-Column Interactive View */}
      <div className="flex flex-1 gap-5 overflow-hidden min-h-0">
        {/* Left Column: Interactive Document Canvas with Full Vertical Scrolling */}
        <div className="flex-1 flex flex-col bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm max-w-[48%] h-full">
          {/* Document Viewer Toolbar (Inspired by Image 4) */}
          <div className="flex flex-wrap items-center justify-between p-2.5 border-b border-slate-200 bg-slate-50 flex-shrink-0 gap-2">
            {/* Page navigation controls */}
            <div className="flex items-center gap-1.5">
              <div className="flex items-center bg-white border border-slate-200 rounded-lg p-0.5 shadow-2xs">
                <button
                  onClick={() => setCurrentPage((p) => Math.max(1, (parsePageNumber(p) || 1) - 1))}
                  disabled={(parsePageNumber(currentPage) || 1) <= 1}
                  className="p-1 text-slate-500 hover:text-slate-800 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer rounded"
                  title="Previous Page"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="font-mono text-xs px-2 font-bold text-slate-700 whitespace-nowrap">
                  Page {parsePageNumber(currentPage) || 1} / {numPages || 1}
                </span>
                <button
                  onClick={() => setCurrentPage((p) => Math.min(numPages || 1, (parsePageNumber(p) || 1) + 1))}
                  disabled={(parsePageNumber(currentPage) || 1) >= (numPages || 1)}
                  className="p-1 text-slate-500 hover:text-slate-800 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer rounded"
                  title="Next Page"
                >
                  <ChevronRight size={14} />
                </button>
              </div>

              {/* Original | Cleaned Segmented Toggle */}
              <div className="flex items-center bg-slate-200/80 p-0.5 rounded-lg border border-slate-200 shadow-2xs text-xs font-semibold">
                <button
                  onClick={() => setViewMode('original')}
                  className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                    viewMode === 'original'
                      ? 'bg-white text-slate-900 font-bold shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                  title="View original unprocessed document"
                >
                  Original
                </button>
                <button
                  onClick={() => setViewMode('cleaned')}
                  className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                    viewMode === 'cleaned'
                      ? 'bg-white text-blue-700 font-bold shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                  title="View contrast-enhanced cleaned preview"
                >
                  Cleaned
                </button>
              </div>
            </div>

            {/* Zoom Controls & Layer Toggles */}
            <div className="flex items-center gap-1.5">
              <div className="flex items-center bg-white border border-slate-200 rounded-lg p-0.5 shadow-2xs">
                <button
                  onClick={handleZoomOut}
                  className="p-1 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Zoom Out"
                >
                  <ZoomOut size={13} />
                </button>
                <span className="text-[11px] font-mono px-1 font-bold text-slate-700 min-w-[36px] text-center">
                  {Math.round(zoomScale * 100)}%
                </span>
                <button
                  onClick={handleZoomIn}
                  className="p-1 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Zoom In"
                >
                  <ZoomIn size={13} />
                </button>
              </div>

              <div className="flex items-center bg-white border border-slate-200 rounded-lg p-0.5 shadow-2xs">
                <button
                  onClick={handleFitPage}
                  className="px-2 py-0.5 text-[10px] font-bold text-slate-600 hover:text-blue-700 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Fit whole page in view"
                >
                  Fit Page
                </button>
                <button
                  onClick={handleFitWidth}
                  className="px-2 py-0.5 text-[10px] font-bold text-slate-600 hover:text-blue-700 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                  title="Fit to width"
                >
                  Fit Width
                </button>
              </div>

              {/* Reference Anchor toggle */}
              {learnedAnchors && (
                <button
                  onClick={() => setShowReferenceAnchors(!showReferenceAnchors)}
                  className={`px-2 py-1 rounded-md text-[10px] font-bold border transition-colors cursor-pointer flex items-center gap-1 ${
                    showReferenceAnchors
                      ? 'bg-indigo-50 text-indigo-800 border-indigo-300'
                      : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-100'
                  }`}
                  title="Toggle dashed ground truth reference anchors"
                >
                  <span>📐 {showReferenceAnchors ? 'Anchors: ON' : 'Anchors: OFF'}</span>
                </button>
              )}
            </div>
          </div>

          {/* Fully Scrollable Canvas Container to view entire PDF correctly */}
          <div
            ref={canvasContainerRef}
            className="flex-1 overflow-hidden bg-slate-100 relative flex flex-col min-h-0"
          >
            <style>{`
              @keyframes docScanSweep {
                0% { top: 0%; opacity: 0.85; }
                50% { opacity: 1; }
                100% { top: 100%; opacity: 0.15; }
              }
            `}</style>
            {currentJob.document_id ? (
              <div
                ref={scrollContainerRef}
                className="flex-1 overflow-y-auto overflow-x-auto p-4 select-none scroll-smooth flex flex-col items-center"
              >
                <div
                  className="relative inline-block shadow-md my-auto bg-white transition-all"
                  style={{
                    width: renderedWidth,
                    minHeight: renderedHeight,
                    filter: viewMode === 'cleaned' ? 'contrast(1.25) saturate(1.1) brightness(1.02)' : 'none',
                  }}
                >
                  <Document
                    file={`${config.API_BASE_URL}/documents/${currentJob.document_id}/file`}
                    onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                    loading={<div className="bg-white" style={{ width: renderedWidth, height: renderedHeight }} />}
                  >
                    <Page
                      pageNumber={parsePageNumber(currentPage) || 1}
                      width={renderedWidth}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                      onLoadSuccess={onPdfPageLoadSuccess}
                    />
                  </Document>

                  <LiveFieldsPanel live={live} />

                  {/* A table cell picked in the review grid */}
                  {tableHighlight && tableHighlight.bbox && tableHighlight.page === (parsePageNumber(currentPage) || 1) && (
                    <div
                      className="absolute border-2 border-fuchsia-500 bg-fuchsia-400/20 rounded pointer-events-none z-40 animate-pulse"
                      style={{
                        left: `${tableHighlight.bbox.x1 * scaleX}px`,
                        top: `${tableHighlight.bbox.y1 * scaleY}px`,
                        width: `${Math.max(12, (tableHighlight.bbox.x2 - tableHighlight.bbox.x1) * scaleX)}px`,
                        height: `${Math.max(10, (tableHighlight.bbox.y2 - tableHighlight.bbox.y1) * scaleY)}px`,
                      }}
                    />
                  )}

                  {/* 1. Ground Truth Reference Bounding Boxes (Dashed Template Baseline) */}
                  {showReferenceAnchors &&
                    learnedAnchors?.fields &&
                    Object.entries(learnedAnchors.fields).map(([refKey, refData]) => {
                      if (!refData?.bbox || refData.bbox.x1 === undefined) return null;
                      const refPage = parsePageNumber(refData.page_number) || 1;
                      if (refPage !== (parsePageNumber(currentPage) || 1)) return null;

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

                  {/* 2. Universal Raw OCR Detections Overlay */}
                  {showAllDetections &&
                    allDetections
                      .filter((det) => (parsePageNumber(det.page_number) || 1) === (parsePageNumber(currentPage) || 1))
                      .map((det) => {
                        if (!det.bbox) return null;
                        const { x1, y1, x2, y2 } = det.bbox;
                        const dLeft = x1 * scaleX;
                        const dTop = y1 * scaleY;
                        const dWidth = (x2 - x1) * scaleX;
                        const dHeight = (y2 - y1) * scaleY;

                        return (
                          <div
                            key={det.id || `det_${det.bbox.x1}_${det.bbox.y1}`}
                            className="absolute border border-dashed border-purple-400/60 bg-purple-500/10 hover:border-purple-600 hover:bg-purple-500/25 transition-all cursor-pointer z-12 group rounded-xs"
                            style={{
                              left: `${dLeft}px`,
                              top: `${dTop}px`,
                              width: `${Math.max(12, dWidth)}px`,
                              height: `${Math.max(8, dHeight)}px`,
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleAddDetectionToTemplate(det);
                            }}
                            title={`Raw OCR: "${det.text}" (${Math.round((det.confidence || 1) * 100)}%) - Click to add to template`}
                          >
                            <span className="opacity-0 group-hover:opacity-100 absolute -top-5 left-0 text-[8px] font-mono font-bold px-1 py-0.5 rounded bg-purple-900 text-white shadow-md pointer-events-none whitespace-nowrap z-30 transition-opacity">
                              + Add "{det.text?.slice(0, 15)}"
                            </span>
                          </div>
                        );
                      })}

                  {/* 2b. The same fields read on THIS page when their main reading is on another page */}
                  {Object.entries(localFields).flatMap(([key, data]) =>
                    (data?.occurrences || [])
                      .filter((o) => o?.bbox && o.bbox.x1 !== undefined && (parsePageNumber(o.page_number) || 1) === (parsePageNumber(currentPage) || 1))
                      .map((o, i) => (
                        <div
                          key={`occ_${key}_${i}`}
                          className={`absolute border-2 border-dashed rounded pointer-events-auto cursor-pointer z-10 ${
                            selectedField === key || hoveredField === key ? 'border-amber-600 bg-amber-400/25' : 'border-amber-500/80 bg-amber-300/10'
                          }`}
                          style={{
                            left: `${o.bbox.x1 * scaleX}px`,
                            top: `${o.bbox.y1 * scaleY}px`,
                            width: `${Math.max(16, (o.bbox.x2 - o.bbox.x1) * scaleX)}px`,
                            height: `${Math.max(12, (o.bbox.y2 - o.bbox.y1) * scaleY)}px`,
                          }}
                          onClick={(e) => { e.stopPropagation(); setSelectedField(key); }}
                          onMouseEnter={() => setHoveredField(key)}
                          onMouseLeave={() => setHoveredField(null)}
                          title={`${key.replace(/_/g, ' ')} (also read on this page): ${o.value}`}
                        >
                          <span className="absolute -top-3.5 left-0 text-[8px] font-mono font-bold px-1 rounded bg-amber-100 text-amber-800 border border-amber-200 pointer-events-none whitespace-nowrap">
                            {key.replace(/_/g, ' ')} · p{o.page_number}
                          </span>
                        </div>
                      ))
                  )}

                  {/* 3. Interactive Bounding Box Overlays for Actual Detected Values */}
                  {Object.entries(localFields).map(([key, data]) => {
                    if (!data?.bbox || data.bbox.x1 === undefined) return null;
                    const fieldPage =
                      parsePageNumber(data.page_number) ||
                      parsePageNumber(data.source_page) ||
                      parsePageNumber(learnedAnchors?.fields?.[key]?.page_number) ||
                      1;
                    if (fieldPage !== (parsePageNumber(currentPage) || 1)) return null;

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
                          <span>{data.label || key.replace(/_/g, ' ')}</span>
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

        {/* Right Column: Structured Review & Verification Table (Inspired by Image 4) */}
        <div className="flex-1 flex flex-col min-w-0 h-full bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
          {/* Document Confidence Overview Card */}
          <div className="p-3.5 bg-slate-50 border-b border-slate-200 flex items-center justify-between gap-4 flex-shrink-0">
            <div className="flex items-center gap-3.5">
              <div
                className={`text-3xl font-extrabold font-mono tracking-tight leading-none ${
                  avgConfidence >= 80 ? 'text-emerald-600' : avgConfidence >= 50 ? 'text-amber-600' : 'text-red-500'
                }`}
              >
                {Math.round(avgConfidence)}
              </div>
              <div className="flex flex-col">
                <span className="text-xs font-bold text-slate-800">
                  Document confidence{' '}
                  <span className="font-normal text-slate-400">· average field score (uncalibrated)</span>
                </span>
                <span className="text-[11px] text-slate-500 mt-0.5">
                  <strong className="text-slate-700">{verifiedCount}</strong> of {totalFields} fields checked ·{' '}
                  <strong className="text-amber-700">{flaggedCount}</strong> need review
                </span>
              </div>
            </div>

            {/* Quick mini indicators for key fields */}
            <div className="hidden xl:flex items-center gap-3 border-l border-slate-200 pl-3">
              {fieldKeys.slice(0, 3).map((k) => {
                const sc = Math.round((localFields[k]?.confidence || 0) * 100);
                return (
                  <div key={k} className="flex flex-col gap-0.5 w-24">
                    <span className="text-[9px] font-bold text-slate-500 uppercase truncate">
                      {k.replace(/_/g, ' ')}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <div className="flex-1 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${
                            sc >= 80 ? 'bg-emerald-500' : sc >= 50 ? 'bg-amber-500' : 'bg-red-400'
                          }`}
                          style={{ width: `${sc}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-slate-600 font-bold">{sc}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Tabs Navigation */}
          <div className="flex items-center justify-between border-b border-slate-200 px-4 pt-2 bg-white flex-shrink-0">
            <div className="flex gap-4">
              <button
                className={`pb-2 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'results' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('results')}
              >
                Fields ({allFieldKeys.length})
              </button>
              <button
                className={`pb-2 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'tables' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('tables')}
              >
                Line items ({extractedTables.length})
              </button>
              <button
                className={`pb-2 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'detections' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('detections')}
              >
                All Detections ({allDetections.length})
              </button>
              <button
                className={`pb-2 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'geometry' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('geometry')}
              >
                Learned Geometry
              </button>
              <button
                className={`pb-2 text-xs font-bold relative transition-colors cursor-pointer ${
                  activeTab === 'json' ? 'text-blue-700 border-b-2 border-blue-700' : 'text-slate-500 hover:text-slate-800'
                }`}
                onClick={() => setActiveTab('json')}
              >
                Raw JSON
              </button>
            </div>
            <span className="text-[11px] font-mono text-slate-400 pb-2">
              Showing {extractedCount} extracted values
            </span>
          </div>

          {/* Fields tab: key : value pairs (template fields + pairs found on the page) */}
          {activeTab === 'results' && (
            <KeyValuePanel
              fields={localFields}
              selectedField={selectedField}
              hoveredField={hoveredField}
              onSelect={handleSelectField}
              onHover={setHoveredField}
              editingField={editingField}
              editValue={editValue}
              setEditValue={setEditValue}
              onStartEdit={startEditing}
              onSaveEdit={saveEditing}
              onCancelEdit={cancelEditing}
              onCopy={handleCopy}
              copiedKey={copiedKey}
              onReOcr={handleReOcr}
              reOcrLoading={reOcrLoading}
              onRemove={handleRemoveField}
              onShowOccurrence={handleShowOccurrence}
              onUseOccurrence={handleUseOccurrence}
              rowRefs={fieldRefs}
            />
          )}

          {/* All Detections Tab Content */}
          {activeTab === 'detections' && (
            <div className="flex-1 flex flex-col min-h-0 bg-slate-50/50">
              {/* Header filter bar */}
              <div className="p-3 bg-white border-b border-slate-200 flex items-center justify-between gap-3 flex-shrink-0">
                <div className="relative flex-1">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input
                    type="text"
                    placeholder="Search raw detected OCR tokens..."
                    className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500 focus:bg-white transition-all font-mono"
                    value={detectionSearch}
                    onChange={(e) => setDetectionSearch(e.target.value)}
                  />
                  {detectionSearch && (
                    <button
                      onClick={() => setDetectionSearch('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 cursor-pointer"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>
                <span className="text-[11px] font-mono text-slate-500 whitespace-nowrap">
                  {allDetections.filter(d => !detectionSearch || (d.text && d.text.toLowerCase().includes(detectionSearch.toLowerCase()))).length} / {allDetections.length} tokens
                </span>
              </div>

              {/* Detections List */}
              <div className="flex-1 overflow-y-auto p-4 space-y-2.5">
                {allDetections.length === 0 ? (
                  <div className="p-8 text-center text-xs text-slate-400 italic">
                    No raw OCR tokens recorded for this job.
                  </div>
                ) : (
                  allDetections
                    .filter(d => !detectionSearch || (d.text && d.text.toLowerCase().includes(detectionSearch.toLowerCase())))
                    .map((det, idx) => {
                      const detPage = parsePageNumber(det.page_number) || 1;
                      const isCurrentPage = detPage === (parsePageNumber(currentPage) || 1);
                      return (
                        <div
                          key={det.id || idx}
                          className={`p-3 bg-white border rounded-xl flex items-center justify-between gap-3 shadow-2xs hover:border-blue-300 transition-all ${
                            isCurrentPage ? 'border-slate-200' : 'border-slate-200/60 opacity-80'
                          }`}
                        >
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-xs font-mono font-bold text-slate-900 truncate">
                                "{det.text}"
                              </span>
                              <span className="text-[9px] font-mono px-1.5 py-0.2 rounded bg-purple-50 text-purple-700 border border-purple-200 font-bold">
                                {Math.round((det.confidence || 1) * 100)}% Conf
                              </span>
                              <button
                                onClick={() => setCurrentPage(detPage)}
                                className={`text-[9px] font-mono px-1.5 py-0.2 rounded font-bold cursor-pointer transition-colors ${
                                  isCurrentPage
                                    ? 'bg-blue-100 text-blue-800 border border-blue-200'
                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200 border border-slate-200'
                                }`}
                                title="Click to view this token's page on canvas"
                              >
                                Page {detPage}
                              </button>
                            </div>
                            {det.bbox && (
                              <div className="text-[10px] font-mono text-slate-400">
                                Bbox: ({det.bbox.x1}, {det.bbox.y1}) - ({det.bbox.x2}, {det.bbox.y2})
                              </div>
                            )}
                          </div>

                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => handleCopy(`det_${idx}`, det.text)}
                              className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded transition-colors cursor-pointer"
                              title="Copy text"
                            >
                              {copiedKey === `det_${idx}` ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}
                            </button>
                            <button
                              onClick={() => handleAddDetectionToTemplate(det)}
                              className="px-2.5 py-1 bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 rounded-lg text-xs font-bold flex items-center gap-1 shadow-2xs transition-all cursor-pointer whitespace-nowrap"
                              title="Add this token as a template field"
                            >
                              <PlusCircle size={12} />
                              <span>+ Add as Field</span>
                            </button>
                          </div>
                        </div>
                      );
                    })
                )}
              </div>
            </div>
          )}

          {/* Tables tab: editable grids */}
          {activeTab === 'tables' && (
            <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-50/50">
              {extractedTables.length === 0 ? (
                <div className="p-12 text-center bg-white border border-slate-200 rounded-xl shadow-xs">
                  <TableIcon size={32} className="mx-auto text-slate-300 mb-2" />
                  <div className="text-sm font-bold text-slate-700">No tables found</div>
                  <div className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                    Tables appear here as an editable grid when this template has a table field and one is detected on the page.
                  </div>
                </div>
              ) : (
                extractedTables.map((tbl, tIdx) => (
                  <TableGrid
                    key={tbl.table_index ?? tIdx}
                    table={tbl}
                    tableNumber={tIdx + 1}
                    jobId={currentJob.job_id}
                    onTableUpdated={handleTableUpdated}
                    onToast={showToast}
                    refine={live.needsResync && refineView?.status === 'running' ? null : refineView}
                    onFocusRows={(ti, from, to) => { extractService.sendFocus(currentJob.job_id, ti, from, to).catch(() => {}); }}
                    onLocateCell={handleLocateCell}
                  />
                ))
              )}
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
