import api from './api';

export const documentsService = {
  list: async (params = {}) => {
    return await api.get('/documents/', { params });
  },

  get: async (id) => {
    return await api.get(`/documents/${id}`);
  }
};
