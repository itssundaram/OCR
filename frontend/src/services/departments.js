/**
 * DOCINT — Departments Service
 */
import api from './api';

export const departmentsService = {
  list: () => api.get('/departments'),
  listAll: () => api.get('/departments/all'),
  create: (data) => api.post('/departments', data),
  update: (slug, data) => api.patch(`/departments/${slug}`, data),
  deactivate: (slug) => api.post(`/departments/${slug}/deactivate`),
};
