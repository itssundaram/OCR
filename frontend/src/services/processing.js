import api from './api';

export const processingService = {
  list: async (params = {}) => {
    return await api.get('/processing/', { params });
  },

  getJob: async (jobId) => {
    return await api.get(`/processing/${jobId}`);
  },
  
  getPipelineSteps: async (jobId) => {
    return await api.get(`/processing/${jobId}/pipeline-steps`);
  }
};
