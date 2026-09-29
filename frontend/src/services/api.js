/**
 * DOCINT — API Client
 * Axios instance with envelope unwrapping and structured error handling.
 */
import axios from 'axios';
import { config } from '../config';

const api = axios.create({
  baseURL: config.API_BASE_URL,
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (body && typeof body === 'object' && 'success' in body) {
      if (body.success) return { data: body.data, meta: body.meta };
    }
    return response.data;
  },
  (error) => {
    const structured = error.response?.data?.error;
    if (structured) {
      const appError = new Error(structured.message);
      appError.code = structured.code;
      appError.details = structured.details;
      appError.status = error.response.status;
      throw appError;
    }
    const networkError = new Error(
      error.code === 'ECONNABORTED'
        ? 'Request timed out.'
        : 'Network error. Check that the backend is running.'
    );
    networkError.code = 'NETWORK_ERROR';
    networkError.status = 0;
    throw networkError;
  }
);

export default api;
