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
    // Case 1: the backend responded with our structured { success, error } envelope.
    const structured = error.response?.data?.error;
    if (structured) {
      const appError = new Error(structured.message);
      appError.code = structured.code;
      appError.details = structured.details;
      appError.status = error.response.status;
      throw appError;
    }

    // Case 2: the backend DID respond, but not with our envelope — a raw
    // FastAPI validation error (422), an unhandled exception (500, with
    // `detail`), a 404 from a route that doesn't exist, a CORS-blocked
    // response, etc. This is NOT "backend unreachable" and must not be
    // reported as such — that was misleading every real server-side error
    // as a connectivity problem.
    if (error.response) {
      const data = error.response.data;
      let message;
      if (typeof data === 'string' && data) message = data;
      else if (data?.detail) message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      else message = `Request failed with status ${error.response.status}`;
      const appError = new Error(message);
      appError.code = 'HTTP_ERROR';
      appError.status = error.response.status;
      appError.raw = data;
      throw appError;
    }

    // Case 3: no response at all — the request never reached a server
    // (backend down, wrong port, DNS/proxy failure) or it timed out.
    const networkError = new Error(
      error.code === 'ECONNABORTED'
        ? 'Request timed out.'
        : 'Network error. Check that the backend is running.'
    );
    networkError.code = error.code === 'ECONNABORTED' ? 'TIMEOUT' : 'NETWORK_ERROR';
    networkError.status = 0;
    throw networkError;
  }
);

export default api;
