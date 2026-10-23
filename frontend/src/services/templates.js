import api from './api';

export const templatesService = {
  listTemplates: async (departmentSlug) => {
    const query = departmentSlug ? `?department_slug=${departmentSlug}` : '';
    return await api.get(`/templates${query}`);
  },

  getTemplate: async (templateId) => {
    return await api.get(`/templates/id/${templateId}`);
  },

  createTemplate: async (departmentSlug, data) => {
    return await api.post(`/templates/${departmentSlug}`, data);
  },

  activateTemplate: async (templateId) => {
    return await api.post(`/templates/${templateId}/activate`);
  },

  activate: async (templateId) => {
    return await api.post(`/templates/${templateId}/activate`);
  },

  deactivateTemplate: async (templateId) => {
    return await api.post(`/templates/${templateId}/deactivate`);
  },

  deactivate: async (templateId) => {
    return await api.post(`/templates/${templateId}/deactivate`);
  },

  updateTemplate: async (templateId, data) => {
    return await api.put(`/templates/${templateId}`, data);
  },

  deleteTemplate: async (templateId) => {
    return await api.delete(`/templates/${templateId}`);
  },

  // jobId lets the server learn each field's printed label + value shape from that job's OCR words.
  lockAnchors: async (templateId, fields, pageWidth = 1654, pageHeight = 2338, jobId = null, expectedVersion = null) => {
    return await api.post(`/templates/${templateId}/lock-anchors`, {
      fields,
      page_width: pageWidth,
      page_height: pageHeight,
      job_id: jobId,
      expected_version: expectedVersion,
    });
  },

  rollbackAnchors: async (templateId, toVersion = null) => {
    return await api.post(`/templates/${templateId}/rollback-anchors`, null, {
      params: toVersion ? { to_version: toVersion } : undefined,
    });
  },

  unlockAnchors: async (templateId) => {
    return await api.post(`/templates/${templateId}/unlock-anchors`);
  },

  resetAnchors: async (templateId) => {
    return await api.delete(`/templates/${templateId}/learned-anchors`);
  }
};
