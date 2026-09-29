/**
 * DOCINT — Centralized Frontend Configuration
 * All env access goes through here. Never use import.meta.env elsewhere.
 */
export const config = {
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  APP_NAME: import.meta.env.VITE_APP_NAME || 'DocInt',
  APP_VERSION: import.meta.env.VITE_APP_VERSION || '1.0.0',
  POLLING_INTERVAL_MS: parseInt(import.meta.env.VITE_POLLING_INTERVAL || '2000', 10),
  MAX_POLL_ATTEMPTS: parseInt(import.meta.env.VITE_MAX_POLL_ATTEMPTS || '120', 10),
};

export default config;
