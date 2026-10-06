import api from './api';
import { config } from '../config';

export const evidenceService = {
  get: (jobId, fieldName) => api.get(`/processing/${jobId}/evidence/${encodeURIComponent(fieldName)}`),
  imageUrl: (jobId, fieldName) =>
    `${config.API_BASE_URL}/processing/${jobId}/evidence/${encodeURIComponent(fieldName)}/image`,
};
