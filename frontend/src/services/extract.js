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
  uploadWithPipeline: (department, templateCode, pipelineName, file, forceRelearn = false) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('force_relearn', forceRelearn ? 'true' : 'false');
    return api.post(
      `/${department.toLowerCase()}/${templateCode.toLowerCase()}/pipeline/${pipelineName}`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
  },

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
  updateField: (jobId, fieldKey, value, bbox = null) =>
    api.post('/extract/update-field', {
      job_id: jobId,
      field_key: fieldKey,
      value,
      bbox,
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

