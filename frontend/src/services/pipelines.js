/**
 * DOCINT — Pipeline Listing Service (Phase 13)
 * Backs the OCR page's single-select dropdown and the Orchestration page's
 * multi-select — reads the live pipeline registry from the backend.
 */
import api from './api';

export const pipelinesService = {
  list: () => api.get('/pipelines'),
  // Phase 11 — lightweight worker/GPU health report (scoped-down worker registry)
  health: () => api.get('/workers/health'),
};
