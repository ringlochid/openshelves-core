/**
 * API Client for OpenShelves Test Frontend
 * Handles authentication, API calls, and logging
 */

// ============================================
// Configuration - Hardcoded App Runner URLs
// ============================================
const CONFIG = {
  LIBRARY_URL: 'https://ppmrpzxpd4.ap-southeast-2.awsapprunner.com',
  AUTH_URL: 'https://enx4hyajcj.ap-southeast-2.awsapprunner.com',
};

// ============================================
// State Management
// ============================================
const state = {
  accessToken: null,
  user: null,
  scopes: [],
  roles: [],
};

// ============================================
// Logging
// ============================================
function log(type, method, url, data = null, duration = null) {
  const logContent = document.getElementById('logContent');
  if (!logContent) return;

  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;

  const time = new Date().toLocaleTimeString();
  let html = `<div class="log-time">${time}${duration ? ` (${duration}ms)` : ''}</div>`;
  html += `<div><span class="log-method">${method}</span> ${url}</div>`;

  if (data) {
    const displayData = typeof data === 'object' ? JSON.stringify(data, null, 2) : data;
    const truncated = displayData.length > 800 ? displayData.slice(0, 800) + '\n...(truncated)' : displayData;
    html += `<pre>${truncated}</pre>`;
  }

  entry.innerHTML = html;
  logContent.insertBefore(entry, logContent.firstChild);

  while (logContent.children.length > 50) {
    logContent.removeChild(logContent.lastChild);
  }
}

function clearLog() {
  const logContent = document.getElementById('logContent');
  if (logContent) logContent.innerHTML = '';
}

// ============================================
// Toast Notifications
// ============================================
function toast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `<span>${type === 'success' ? '✓' : type === 'error' ? '✕' : 'ℹ'}</span> ${message}`;
  container.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 200); }, 3000);
}

// ============================================
// API Request Helper
// ============================================
async function apiRequest(baseUrl, method, path, body = null, useAuth = true) {
  const url = `${baseUrl}${path}`;
  const start = Date.now();

  const headers = { 'Content-Type': 'application/json' };
  if (useAuth && state.accessToken) {
    headers['Authorization'] = `Bearer ${state.accessToken}`;
  }

  const options = { method, headers, credentials: 'include' };
  if (body && method !== 'GET') {
    options.body = JSON.stringify(body);
  }

  log('', method, path, body);

  try {
    const response = await fetch(url, options);
    const duration = Date.now() - start;

    // Handle 204 No Content and empty responses gracefully
    let data;
    const text = await response.text();
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }

    if (!response.ok) {
      log('error', `← ${response.status}`, path, data, duration);
      throw { status: response.status, data, message: data?.detail || 'Request failed' };
    }

    log('success', `← ${response.status}`, path, data, duration);
    return data;
  } catch (error) {
    if (error.status) throw error;
    log('error', 'ERROR', path, error.message);
    throw error;
  }
}

// ============================================
// Auth Service API
// ============================================
const authApi = {
  async register(name, email, password) {
    return apiRequest(CONFIG.AUTH_URL, 'POST', '/auth/register', { name, email, password }, false);
  },

  async login(identifier, password) {
    const body = identifier.includes('@') ? { email: identifier, password } : { name: identifier, password };
    const data = await apiRequest(CONFIG.AUTH_URL, 'POST', '/auth/login', body, false);
    if (data.access_token) {
      state.accessToken = data.access_token;
      state.user = data.user;
      try {
        const payload = JSON.parse(atob(data.access_token.split('.')[1]));
        state.scopes = payload.scopes || [];
        state.roles = payload.roles || [];
      } catch (e) { }
      updateAuthUI();
    }
    return data;
  },

  async refresh() {
    const data = await apiRequest(CONFIG.AUTH_URL, 'POST', '/auth/refresh', null, false);
    if (data.access_token) {
      state.accessToken = data.access_token;
      try {
        const payload = JSON.parse(atob(data.access_token.split('.')[1]));
        state.scopes = payload.scopes || [];
        state.roles = payload.roles || [];
      } catch (e) { }
      updateAuthUI();
    }
    return data;
  },

  async logout(all = false) {
    await apiRequest(CONFIG.AUTH_URL, 'POST', `/auth/logout?all=${all}`, null, false);
    state.accessToken = null; state.user = null; state.scopes = []; state.roles = [];
    updateAuthUI();
  },

  async sendVerifyEmail(email) { return apiRequest(CONFIG.AUTH_URL, 'POST', '/auth/verify-email/send', { email }, false); },
  async verifyEmail(token) { return apiRequest(CONFIG.AUTH_URL, 'GET', `/auth/verify-email?token=${encodeURIComponent(token)}`, null, false); },
  async getSessions() { return apiRequest(CONFIG.AUTH_URL, 'GET', '/auth/sessions'); },
  async getMe() { return apiRequest(CONFIG.AUTH_URL, 'GET', '/user/me'); },
  async updateProfile(data) { return apiRequest(CONFIG.AUTH_URL, 'PATCH', '/user/me/update', data); },
  async getProfile(userId) { return apiRequest(CONFIG.AUTH_URL, 'GET', `/user/profile?user_id=${userId}`, null, false); },
  async presignAvatar(contentType) { return apiRequest(CONFIG.AUTH_URL, 'POST', '/auth/avatar/upload', { content_type: contentType }); },
  async commitAvatar(key) { return apiRequest(CONFIG.AUTH_URL, 'POST', '/auth/avatar/commit', { key }); },
  async submitReport(target, reason, category) { return apiRequest(CONFIG.AUTH_URL, 'POST', '/reports', { target, reason, category }); },
  async listReports(status = null, limit = 50) {
    let path = `/reports?limit=${limit}`;
    if (status) path += `&status=${status}`;
    return apiRequest(CONFIG.AUTH_URL, 'GET', path);
  },
};

// ============================================
// OpenShelves API
// ============================================
const libraryApi = {
  // Books
  async listBooks(params = {}) {
    const q = new URLSearchParams();
    if (params.q) q.set('q', params.q);
    if (params.tags) params.tags.forEach(t => q.append('tags', t));
    if (params.exclude_tags) params.exclude_tags.forEach(t => q.append('exclude_tags', t));
    if (params.before) q.set('before', params.before);
    if (params.after) q.set('after', params.after);
    if (params.sort) params.sort.forEach(s => q.append('sort', s));
    if (params.limit) q.set('limit', params.limit);
    if (params.cursor) q.set('cursor', params.cursor);
    return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/books?${q.toString()}`, null, false);
  },
  async getBook(id) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/books/${id}`, null, !!state.accessToken); },
  async getMyBooks(page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/books/me?page=${page}`); },
  async createBook(data) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', '/books', data); },
  async updateBook(id, data) { return apiRequest(CONFIG.LIBRARY_URL, 'PATCH', `/books/${id}`, data); },
  async deleteBook(id) { return apiRequest(CONFIG.LIBRARY_URL, 'DELETE', `/books/${id}`); },
  async getBookReviews(bookId) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/books/${bookId}/reviews`, null, false); },
  async createReview(bookId, rating, comment = null) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/${bookId}/reviews`, { rating, comment }); },
  async voteReview(reviewId, vote) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/reviews/${reviewId}/vote`, { vote }); },
  async approveBook(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/${id}/approve`); },
  async rejectBook(id, reason) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/${id}/reject?reason=${encodeURIComponent(reason)}`); },
  async rollbackBook(id, targetVersion, currentVersion) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/${id}/rollback`, { target_version: targetVersion, version: currentVersion }); },

  // Authors
  async listAuthors(params = {}) {
    const q = new URLSearchParams();
    if (params.search) q.set('search', params.search);
    if (params.limit) q.set('limit', params.limit);
    if (params.cursor) q.set('cursor', params.cursor);
    return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/authors?${q.toString()}`, null, false);
  },
  async getAuthor(id) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/authors/${id}`, null, !!state.accessToken); },
  async getMyAuthors(page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/authors/me?page=${page}`); },
  async createAuthor(data) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', '/authors', data); },
  async updateAuthor(id, data) { return apiRequest(CONFIG.LIBRARY_URL, 'PATCH', `/authors/${id}`, data); },
  async deleteAuthor(id) { return apiRequest(CONFIG.LIBRARY_URL, 'DELETE', `/authors/${id}`); },
  async followAuthor(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/authors/${id}/follow`); },
  async unfollowAuthor(id) { return apiRequest(CONFIG.LIBRARY_URL, 'DELETE', `/authors/${id}/follow`); },
  async rollbackAuthor(id, targetVersion, currentVersion) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/authors/${id}/rollback`, { target_version: targetVersion, version: currentVersion }); },

  // Collections
  async listCollections(params = {}) {
    const q = new URLSearchParams();
    if (params.q) q.set('q', params.q);
    if (params.sort) params.sort.forEach(s => q.append('sort', s));
    if (params.limit) q.set('limit', params.limit);
    if (params.cursor) q.set('cursor', params.cursor);
    return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/collections?${q.toString()}`, null, false);
  },
  async getCollection(id) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/collections/${id}`, null, !!state.accessToken); },
  async getMyCollections(page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/collections/me?page=${page}`); },
  async createCollection(data) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', '/collections', data); },
  async updateCollection(id, data) { return apiRequest(CONFIG.LIBRARY_URL, 'PATCH', `/collections/${id}`, data); },
  async deleteCollection(id) { return apiRequest(CONFIG.LIBRARY_URL, 'DELETE', `/collections/${id}`); },
  async addBookToCollection(collectionId, bookId, position = null) {
    const data = { book_id: bookId };
    if (position !== null) data.position = position;
    return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/collections/${collectionId}/books`, data);
  },
  async removeBookFromCollection(collectionId, bookId) { return apiRequest(CONFIG.LIBRARY_URL, 'DELETE', `/collections/${collectionId}/books/${bookId}`); },
  async rollbackCollection(id, targetVersion, currentVersion) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/collections/${id}/rollback`, { target_version: targetVersion, version: currentVersion }); },

  // Jury
  async getPendingAuthors(page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/jury/authors?page=${page}`); },
  async getPendingBooks(page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/jury/books?page=${page}`); },
  async getPendingCollections(page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/jury/collections?page=${page}`); },
  async voteOnAuthor(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/jury/authors/${id}/vote`); },
  async voteOnBook(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/jury/books/${id}/vote`); },
  async voteOnCollection(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/jury/collections/${id}/vote`); },
  // Curator approve/reject (requires jury:override scope)
  async approveAuthor(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/authors/${id}/approve`); },
  async rejectAuthor(id, reason) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/authors/${id}/reject?reason=${encodeURIComponent(reason)}`); },
  async approveBook(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/${id}/approve`); },
  async rejectBook(id, reason) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/books/${id}/reject?reason=${encodeURIComponent(reason)}`); },
  async approveCollection(id) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/collections/${id}/approve`); },
  async rejectCollection(id, reason) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/collections/${id}/reject?reason=${encodeURIComponent(reason)}`); },

  // History
  async getBookHistory(bookId, page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/books/${bookId}/history?page=${page}`, null, !!state.accessToken); },
  async getAuthorHistory(authorId, page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/authors/${authorId}/history?page=${page}`, null, !!state.accessToken); },
  async getCollectionHistory(collectionId, page = 1) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/collections/${collectionId}/history?page=${page}`, null, !!state.accessToken); },
  async getHistoryDetail(historyId) { return apiRequest(CONFIG.LIBRARY_URL, 'GET', `/history/${historyId}`, null, !!state.accessToken); },

  // Uploads
  async presignBookCover(bookId, contentType) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/books/${bookId}/cover`, { content_type: contentType }); },
  async commitBookCover(bookId, uploadId, s3Key) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/books/${bookId}/cover/commit`, { upload_id: uploadId, s3_key: s3Key }); },
  async presignBookFile(bookId, contentType, filename) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/books/${bookId}/file`, { content_type: contentType, filename: filename }); },
  async commitBookFile(bookId, uploadId, s3Key) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/books/${bookId}/file/commit`, { upload_id: uploadId, s3_key: s3Key }); },
  async presignAuthorAvatar(authorId, contentType) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/authors/${authorId}/avatar`, { content_type: contentType }); },
  async commitAuthorAvatar(authorId, uploadId, s3Key) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/authors/${authorId}/avatar/commit`, { upload_id: uploadId, s3_key: s3Key }); },
  async presignCollectionCover(collectionId, contentType) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/collections/${collectionId}/cover`, { content_type: contentType }); },
  async commitCollectionCover(collectionId, uploadId, s3Key) { return apiRequest(CONFIG.LIBRARY_URL, 'POST', `/uploads/collections/${collectionId}/cover/commit`, { upload_id: uploadId, s3_key: s3Key }); },

  async health() { return apiRequest(CONFIG.LIBRARY_URL, 'GET', '/ready', null, false); },
};

// ============================================
// S3 Upload Helper
// ============================================
async function uploadToS3(presignData, file) {
  const formData = new FormData();
  Object.entries(presignData.fields).forEach(([key, value]) => formData.append(key, value));
  formData.append('file', file);

  log('', 'POST', presignData.url, `Uploading ${file.name} (${file.size} bytes)`);

  const response = await fetch(presignData.url, { method: 'POST', body: formData });
  if (!response.ok) {
    log('error', `← ${response.status}`, 'S3 Upload', await response.text());
    throw new Error('S3 upload failed');
  }
  log('success', `← ${response.status}`, 'S3 Upload', 'Success');
  return true;
}

// ============================================
// UI Helpers
// ============================================
function updateAuthUI() {
  const statusDot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');
  const scopesDisplay = document.getElementById('scopesDisplay');
  const logoutBtn = document.getElementById('logoutBtn');

  if (state.accessToken && state.user) {
    statusDot?.classList.add('online');
    if (statusText) statusText.textContent = state.user.name || state.user.email;
    if (scopesDisplay) scopesDisplay.textContent = `Roles: ${state.roles.join(', ')}`;
    logoutBtn?.classList.remove('hidden');
  } else {
    statusDot?.classList.remove('online');
    if (statusText) statusText.textContent = 'Not logged in';
    if (scopesDisplay) scopesDisplay.textContent = '';
    logoutBtn?.classList.add('hidden');
  }
}

function hasScope(scope) { return state.scopes.includes(scope); }
function hasRole(role) { return state.roles.includes(role); }
function isLoggedIn() { return !!state.accessToken; }

function navigateTo(pageId) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const page = document.getElementById(`page-${pageId}`);
  if (page) page.classList.add('active');

  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const navItem = document.querySelector(`.nav-item[data-page="${pageId}"]`);
  if (navItem) navItem.classList.add('active');

  if (window.pageHandlers && window.pageHandlers[pageId]) {
    window.pageHandlers[pageId]();
  }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => navigateTo(item.dataset.page));
  });
  document.getElementById('clearLogBtn')?.addEventListener('click', clearLog);
  updateAuthUI();
});

// Export
window.CONFIG = CONFIG;
window.state = state;
window.authApi = authApi;
window.libraryApi = libraryApi;
window.uploadToS3 = uploadToS3;
window.toast = toast;
window.log = log;
window.navigateTo = navigateTo;
window.hasScope = hasScope;
window.hasRole = hasRole;
window.isLoggedIn = isLoggedIn;
window.updateAuthUI = updateAuthUI;
window.clearLog = clearLog;
