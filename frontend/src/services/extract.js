/**
 * DOCINT — Extraction Service
 * Handles the department-based extraction API.
 */
import axios from 'axios';
import { config } from '../config';

const api = axios.create({
  baseURL: config.API_BASE_URL,
  timeout: 120000,
});

api.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (body && typeof body === 'object' && 'success' in body) {
      if (body.success) return { data: body.data };
    }
    return response.data;
  },
  (error) => {
    const structured = error.response?.data?.error;
    if (structured) {
      const appError = new Error(structured.message);
      appError.code = structured.code;
      throw appError;
    }
    throw new Error('Network error. Check that the backend is running.');
  }
);

export const extractService = {
  // Upload document for extraction
  upload: (department, templateCode, file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(
      `/${department.toLowerCase()}/${templateCode.toLowerCase()}`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
  },

  // Poll extraction status
  getStatus: (department, templateCode, jobId) =>
    api.get(`/${department.toLowerCase()}/${templateCode.toLowerCase()}/status/${jobId}`),

  // List endpoints for a dept/template
  listEndpoints: (department, templateCode) =>
    api.get(`/${department.toLowerCase()}/${templateCode.toLowerCase()}/endpoints`),
};
