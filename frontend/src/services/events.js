import api from './api';

export const eventsService = {
  listForJob: (jobId) => api.get(`/processing/${jobId}/events`),
  recent: (params = {}) => api.get('/events/recent', { params }),
};
