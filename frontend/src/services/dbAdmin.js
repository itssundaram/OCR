/**
 * DOCINT — Database page service (backend: /db-admin, off unless DB_ADMIN_UI_ENABLED=true).
 * The password goes to the server once, in connect(); every later call carries only the session id.
 */
import api from './api';

const s = (sid) => `/db-admin/sessions/${encodeURIComponent(sid)}`;

export const dbAdminService = {
  status: () => api.get('/db-admin/status'),
  profiles: () => api.get('/db-admin/profiles'),
  saveProfile: (name, params, profileId) => api.post('/db-admin/profiles', { name, params, profile_id: profileId || null }),
  deleteProfile: (id) => api.delete(`/db-admin/profiles/${encodeURIComponent(id)}`),
  connect: ({ profileId, params, saveAs }) =>
    api.post('/db-admin/connect', { profile_id: profileId || null, params: params || null, save_as: saveAs || null }),
  disconnect: (sid) => api.post(`${s(sid)}/disconnect`),
  schemas: (sid, includeSystem = false) => api.get(`${s(sid)}/schemas`, { params: { include_system: includeSystem } }),
  tables: (sid, schema) => api.get(`${s(sid)}/tables`, { params: { schema } }),
  columns: (sid, schema, table) => api.get(`${s(sid)}/columns`, { params: { schema, table } }),
  rows: (sid, schema, table, { limit = 100, offset = 0, orderBy, desc = false } = {}) =>
    api.get(`${s(sid)}/rows`, { params: { schema, table, limit, offset, order_by: orderBy, desc } }),
  count: (sid, schema, table) => api.get(`${s(sid)}/count`, { params: { schema, table } }),
  useAsAppDb: (sid) => api.post(`${s(sid)}/use-as-app-db`),
  clearOverride: () => api.delete('/db-admin/override'),
};
