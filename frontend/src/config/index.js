/**
 * DOCINT — Centralized Frontend Configuration
 * All env access goes through here. Never use import.meta.env elsewhere.
 * The only two variables the frontend reads are listed in frontend/.env.example.
 */
export const config = {
  // Base URL of the REST API. Every request is built from it.
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  // Address shown in the "Generate URL / API" page's copy-ready cURL commands.
  // Empty = the address of this page with the dev-server port swapped for 8000.
  API_HOST: import.meta.env.VITE_API_HOST || '',
};

export default config;
