import api from './api';

export const dashboardService = {
  getStats: async () => {
    return await api.get('/dashboard/stats');
  }
};
