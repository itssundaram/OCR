/**
 * DOCINT — Extraction Service
 * Handles the department-based extraction API.
 */
import axios from 'axios';
import { config } from '../config';

const api = axios.create({
  baseURL: config.API_BASE_URL,
  timeout: 120000,
});

api.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (body && typeof body === 'object' && 'success' in body) {
      if (body.success) return { data: body.data };
    }
    return response.data;
  },
  (error) => {
    const structured = error.response?.data?.error;
    if (structured) {
      const appError = new Error(structured.message);
      appError.code = structured.code;
      throw appError;
    }
    throw new Error('Network error. Check that the backend is running.');
  }
);

export const extractService = {
  // Upload document for extraction
  upload: (department, templateCode, file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(
      `/${department.toLowerCase()}/${templateCode.toLowerCase()}`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
  },

  // Poll extraction status
  getStatus: (department, templateCode, jobId) =>
    api.get(`/${department.toLowerCase()}/${templateCode.toLowerCase()}/status/${jobId}`),

  // List endpoints for a dept/template
  listEndpoints: (department, templateCode) =>
    api.get(`/${department.toLowerCase()}/${templateCode.toLowerCase()}/endpoints`),

  // Phase 13 — OCR page: upload with ONE explicitly chosen pipeline.
  // Only that pipeline processes the document.
  // forceRelearn: the "this is a new format" checkbox on the upload page —
  // tells geometry-learning pipelines (learned_anchor) to ignore any stored
  // pattern for this template and learn fresh from this document instead.
  // opts.prefetch: the upload is a hidden background job until commitPrefetch() is called (see usePrefetchUpload).
  // opts.signal / opts.onUploadProgress: abort the request / learn when all bytes have been sent.
  uploadWithPipeline: (department, templateCode, pipelineName, file, forceRelearn = false, opts = {}) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('force_relearn', forceRelearn ? 'true' : 'false');
    if (opts.prefetch) formData.append('prefetch', 'true');
    return api.post(
      `/${department.toLowerCase()}/${templateCode.toLowerCase()}/pipeline/${pipelineName}`,
      formData,
      {
        headers: { 'Content-Type': 'multipart/form-data' },
        signal: opts.signal,
        onUploadProgress: opts.onUploadProgress,
      }
    );
  },

  // "Execute extraction" on a prefetched document: the hidden job becomes an ordinary one.
  commitPrefetch: (jobId) => api.post(`/extract/jobs/${jobId}/commit`),
  // The person chose another document / template / pipeline: stop and remove the hidden job.
  discardPrefetch: (jobId) => api.post(`/extract/jobs/${jobId}/discard`),
  // Same, for a closing tab (fire-and-forget; the server also reaps forgotten prefetches by itself).
  discardPrefetchBeacon: (jobId) => {
    try {
      if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
        return navigator.sendBeacon(`${config.API_BASE_URL}/extract/jobs/${jobId}/discard`);
      }
    } catch {
      /* the server-side reaper handles it */
    }
    return false;
  },

  // Add a "found on document" field to the job's template for every later document (additive, logged on the server).
  promoteField: (jobId, fieldKey, name = null, fieldType = null) =>
    api.post(`/extract/jobs/${jobId}/promote-field`, { field_key: fieldKey, name, field_type: fieldType }),

  // The result as department-ready JSON (template fields flat, other fields apart, tables as keyed rows).
  // Built by the server from the stored result, so corrections already saved are in it.
  getExport: (jobId, detail = false) => api.get(`/extract/jobs/${jobId}/export${detail ? '?detail=true' : ''}`),

  // Phase 13 — Orchestration page: upload with SEVERAL pipelines selected
  // together. All of them run and are reconciled field-by-field.
  uploadForComparison: (department, templateCode, pipelineNames, file) => {
    const formData = new FormData();
    formData.append('file', file);
    const params = new URLSearchParams();
    pipelineNames.forEach((name) => params.append('pipelines', name));
    return api.post(
      `/${department.toLowerCase()}/${templateCode.toLowerCase()}/compare?${params.toString()}`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
  },

  // Fast Micro-OCR on a user-adjusted bounding box
  reOcrField: (jobId, fieldKey, bbox, pageNumber = 1) =>
    api.post('/extract/re-ocr-field', {
      job_id: jobId,
      field_key: fieldKey,
      bbox,
      page_number: pageNumber,
    }),

  // Manual field value update
  updateField: (jobId, fieldKey, value, bbox = null, extra = {}) =>
    api.post('/extract/update-field', {
      job_id: jobId,
      field_key: fieldKey,
      value,
      bbox,
      ...extra, // page_number / occurrences when the value was taken from another page
    }),

  // Save one corrected table cell back into the job result
  updateTableCell: (jobId, tableIndex, row, col, value) =>
    api.post('/extract/update-table-cell', {
      job_id: jobId,
      table_index: tableIndex,
      row,
      col,
      value,
    }),

  // Process the same document again with the template as it is now (saved anchors included)
  rerunJob: (jobId, forceRelearn = false) =>
    api.post(`/extract/jobs/${jobId}/rerun`, { force_relearn: forceRelearn }),

  // Background refinement: tell the server what the reviewer is looking at (it re-reads that first)
  sendFocus: (jobId, tableIndex, rowFrom, rowTo) =>
    api.post(`/extract/jobs/${jobId}/focus`, { table_index: tableIndex, row_from: rowFrom, row_to: rowTo }),
  getRefine: (jobId) => api.get(`/extract/jobs/${jobId}/refine`),
  cancelRefine: (jobId) => api.post(`/extract/jobs/${jobId}/refine/cancel`),
  // Close-up PNG of one table cell (cut from the page the extraction used)
  cellImageUrl: (jobId, tableIndex, row, col, rev = 0) =>
    `${config.API_BASE_URL}/extract/jobs/${jobId}/tables/${tableIndex}/cells/${row}/${col}/image?v=${rev}`,

  // Add, remove, or update template fields
  manageTemplateField: (jobId, action, fieldKey, value = null, bbox = null, pageNumber = 1, saveToTemplate = true) =>
    api.post('/extract/manage-template-field', {
      job_id: jobId,
      action,
      field_key: fieldKey,
      value,
      bbox,
      page_number: pageNumber,
      save_to_template: saveToTemplate,
    }),
};

