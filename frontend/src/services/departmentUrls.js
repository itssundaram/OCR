/**
 * DOCINT — Generate URL Service
 * Backs the "Generate URL" page: creates/lists shareable department+template
 * pre-filled links against the backend's /department-urls route.
 */
import api from './api';

export const departmentUrlsService = {
  list: (departmentId) =>
    api.get('/department-urls', { params: departmentId ? { department_id: departmentId } : {} }),
  create: (data) => api.post('/department-urls', data),
  deactivate: (id) => api.post(`/department-urls/${id}/deactivate`),
  remove: (id) => api.delete(`/department-urls/${id}`),
};
