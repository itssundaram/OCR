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

  deactivateTemplate: async (templateId) => {
    return await api.post(`/templates/${templateId}/deactivate`);
  },

  updateTemplate: async (templateId, data) => {
    return await api.put(`/templates/${templateId}`, data);
  },

  deleteTemplate: async (templateId) => {
    return await api.delete(`/templates/${templateId}`);
  }
};
