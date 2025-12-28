/**
 * Event Handlers for Library Service Test Frontend
 * All page interactions and API calls
 */

// ============================================
// AUTH HANDLERS
// ============================================
async function handleLogin() {
    const id = document.getElementById('loginId').value;
    const pwd = document.getElementById('loginPwd').value;
    if (!id || !pwd) return toast('Please fill in all fields', 'error');

    try {
        await authApi.login(id, pwd);
        toast('Login successful!', 'success');
    } catch (e) {
        toast(e.message || 'Login failed', 'error');
    }
}

async function handleRegister() {
    const name = document.getElementById('regName').value;
    const email = document.getElementById('regEmail').value;
    const pwd = document.getElementById('regPwd').value;
    if (!name || !email || !pwd) return toast('Please fill in all fields', 'error');

    try {
        await authApi.register(name, email, pwd);
        toast('Registration successful! Please verify your email.', 'success');
    } catch (e) {
        toast(e.message || 'Registration failed', 'error');
    }
}

async function handleLogout() {
    try {
        await authApi.logout();
        toast('Logged out', 'info');
    } catch (e) {
        // Still clear local state even if API fails
        state.accessToken = null;
        state.user = null;
        state.scopes = [];
        state.roles = [];
        updateAuthUI();
        toast('Logged out', 'info');
    }
}

async function handleRefresh() {
    try {
        await authApi.refresh();
        toast('Token refreshed!', 'success');
    } catch (e) {
        toast(e.message || 'Refresh failed', 'error');
    }
}

// ============================================
// EMAIL HANDLERS
// ============================================
async function handleSendVerify() {
    const email = document.getElementById('verifyEmail').value;
    if (!email) return toast('Please enter email', 'error');

    try {
        await authApi.sendVerifyEmail(email);
        toast('Verification email sent!', 'success');
    } catch (e) {
        toast(e.message || 'Failed to send email', 'error');
    }
}

async function handleVerifyToken() {
    const token = document.getElementById('verifyToken').value;
    if (!token) return toast('Please enter token', 'error');

    try {
        await authApi.verifyEmail(token);
        toast('Email verified!', 'success');
    } catch (e) {
        toast(e.message || 'Verification failed', 'error');
    }
}

// ============================================
// PROFILE HANDLERS
// ============================================
async function loadProfile() {
    if (!isLoggedIn()) {
        document.getElementById('profileDisplay').innerHTML = '<span class="text-muted">Login to view profile</span>';
        return;
    }

    try {
        const user = await authApi.getMe();
        document.getElementById('profileDisplay').innerHTML = `
      <div class="mb-4" style="text-align:center">
        ${user.avatar_urls ? `<img src="${user.avatar_urls['256']}" class="avatar" alt="Avatar">` : '<div class="avatar-placeholder">👤</div>'}
      </div>
      <table style="width:100%">
        <tr><td><strong>ID</strong></td><td style="word-break:break-all">${user.id}</td></tr>
        <tr><td><strong>Name</strong></td><td>${user.name}</td></tr>
        <tr><td><strong>Email</strong></td><td>${user.email || 'N/A'}</td></tr>
        <tr><td><strong>Roles</strong></td><td>${(user.roles || []).map(r => `<span class="badge badge-info">${r}</span>`).join(' ')}</td></tr>
        <tr><td><strong>Trust Score</strong></td><td>${user.trust_score}</td></tr>
        <tr><td><strong>Reputation</strong></td><td>${user.reputation_percentage?.toFixed(1)}%</td></tr>
        <tr><td><strong>Bio</strong></td><td>${user.bio || '-'}</td></tr>
        <tr><td><strong>Verified</strong></td><td>${user.email_verified_at ? '<span class="badge badge-success">Yes</span>' : '<span class="badge badge-warning">No</span>'}</td></tr>
      </table>
    `;
    } catch (e) {
        document.getElementById('profileDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

async function handleUpdateProfile() {
    const name = document.getElementById('updateName').value;
    const bio = document.getElementById('updateBio').value;

    const data = {};
    if (name) data.name = name;
    if (bio) data.bio = bio;

    if (Object.keys(data).length === 0) return toast('Nothing to update', 'error');

    try {
        await authApi.updateProfile(data);
        toast('Profile updated!', 'success');
        loadProfile();
    } catch (e) {
        toast(e.message || 'Update failed', 'error');
    }
}

async function handleAvatarUpload() {
    const file = document.getElementById('avatarFile').files[0];
    if (!file) return toast('Please select a file', 'error');

    try {
        // 1. Get presigned URL
        const presign = await authApi.presignAvatar(file.type);

        // 2. Upload to S3
        await uploadToS3(presign, file);

        // 3. Commit
        await authApi.commitAvatar(presign.key);

        toast('Avatar uploaded!', 'success');
        loadProfile();
    } catch (e) {
        toast(e.message || 'Upload failed', 'error');
    }
}

// ============================================
// SESSIONS HANDLERS
// ============================================
async function loadSessions() {
    if (!isLoggedIn()) {
        document.getElementById('sessionsDisplay').innerHTML = '<span class="text-muted">Login to view sessions</span>';
        return;
    }

    try {
        const data = await authApi.getSessions();
        if (!data.sessions || data.sessions.length === 0) {
            document.getElementById('sessionsDisplay').innerHTML = '<span class="text-muted">No active sessions</span>';
            return;
        }

        let html = '<table><thead><tr><th>Device</th><th>IP</th><th>Last Used</th><th>Current</th></tr></thead><tbody>';
        data.sessions.forEach(s => {
            html += `<tr>
        <td>${s.user_agent?.substring(0, 40) || 'Unknown'}...</td>
        <td>${s.ip || '-'}</td>
        <td>${new Date(s.last_used_at).toLocaleString()}</td>
        <td>${s.is_current ? '<span class="badge badge-success">Yes</span>' : ''}</td>
      </tr>`;
        });
        html += '</tbody></table>';
        document.getElementById('sessionsDisplay').innerHTML = html;
    } catch (e) {
        document.getElementById('sessionsDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

// ============================================
// TRUST HANDLERS
// ============================================
async function handleSubmitReport() {
    const contentType = document.getElementById('reportContentType').value;
    const contentId = parseInt(document.getElementById('reportContentId').value);
    const editId = parseInt(document.getElementById('reportEditId').value);
    const actorId = document.getElementById('reportActorId').value;
    const category = document.getElementById('reportCategory').value;
    const reason = document.getElementById('reportReason').value;

    if (!contentId || !editId || !actorId || !reason) {
        return toast('Please fill in all fields', 'error');
    }

    const target = {
        content_type: contentType,
        content_id: contentId,
        edit_id: editId,
        action: 'UPDATE', // Default to UPDATE
        actor_id: actorId
    };

    try {
        await authApi.submitReport(target, reason, category);
        toast('Report submitted!', 'success');
    } catch (e) {
        toast(e.message || 'Report failed', 'error');
    }
}

// ============================================
// BOOKS HANDLERS
// ============================================
async function searchBooks() {
    const params = {};
    const q = document.getElementById('bookSearch').value;
    const tags = document.getElementById('bookTags').value;
    const after = document.getElementById('bookAfter').value;
    const sort = document.getElementById('bookSort').value;

    if (q) params.q = q;
    if (tags) params.tags = tags.split(',').map(t => t.trim());
    if (after) params.after = parseInt(after);
    if (sort) params.sort = [sort];
    params.limit = 20;

    try {
        const data = await libraryApi.listBooks(params);
        renderBooks(data.items || []);
    } catch (e) {
        toast(e.message || 'Search failed', 'error');
    }
}

function renderBooks(books) {
    const container = document.getElementById('booksResults');
    if (books.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="icon">📚</div>No books found</div>';
        return;
    }

    container.innerHTML = books.map(b => `
    <div class="item-card" onclick="viewBook(${b.id})">
      <div class="item-card-title">${b.title}</div>
      <div class="item-card-meta">
        Year: ${b.year || '-'} | Rating: ${b.average_rating?.toFixed(1) || '-'} ⭐
        <br>Views: ${b.view_count || 0} | Trending: ${b.trending_score?.toFixed(2) || 0}
      </div>
      <div class="tags mt-2">
        ${(b.tags || []).slice(0, 3).map(t => `<span class="tag">${t}</span>`).join('')}
      </div>
    </div>
  `).join('');
}

function viewBook(id) {
    document.getElementById('bookDetailId').value = id;
    navigateTo('book-detail');
    loadBookDetail();
}

async function loadBookDetail() {
    const id = document.getElementById('bookDetailId').value;
    if (!id) return;

    try {
        const book = await libraryApi.getBook(id);
        document.getElementById('bookDetailDisplay').classList.remove('hidden');
        document.getElementById('bookReviewsSection').classList.remove('hidden');

        // Build cover image HTML - use available size from cover_urls
        const coverUrl = book.cover_urls?.['800x800'] || book.cover_urls?.['1200x1800'] || Object.values(book.cover_urls || {})[0] || null;
        const coverHtml = coverUrl
            ? `<img src="${coverUrl}" alt="${book.title}" style="width:180px;height:auto;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,0.3);">`
            : `<div style="width:180px;height:250px;background:var(--bg-tertiary);border-radius:12px;display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:3rem;">📖</div>`;

        // Build download button if file exists (uses file_url from FileKeyMixin)
        const downloadHtml = book.file_url
            ? `<a href="${book.file_url}" target="_blank" class="btn btn-success" style="margin-top:12px;width:100%;">📥 Download ${book.file_format?.toUpperCase() || 'File'}</a>`
            : '';

        // Get review count from embedded reviews array
        const reviewCount = book.reviews?.length || 0;

        document.getElementById('bookDetailContent').innerHTML = `
      <div style="display:flex;gap:24px;align-items:flex-start;">
        <div style="flex-shrink:0;">
          ${coverHtml}
          ${downloadHtml}
        </div>
        <div style="flex:1;">
          <h3 style="margin:0;font-size:1.5rem;color:var(--text-primary);">${book.title}</h3>
          <span class="badge badge-${book.status?.toLowerCase() || 'pending'}" style="margin-top:8px;display:inline-block;">${book.status}</span>
          
          <div style="margin-top:16px;display:grid;grid-template-columns:auto 1fr;gap:8px 16px;color:var(--text-secondary);">
            <span style="color:var(--text-muted);">Authors</span>
            <span>${(book.authors || []).map(a => a.name).join(', ') || '-'}</span>
            
            <span style="color:var(--text-muted);">Year</span>
            <span>${book.year || '-'}</span>
            
            <span style="color:var(--text-muted);">Rating</span>
            <span>${book.average_rating?.toFixed(1) || '0.0'} ⭐ (${reviewCount} reviews)</span>
            
            <span style="color:var(--text-muted);">Views</span>
            <span>${book.view_count || 0}</span>
            
            <span style="color:var(--text-muted);">Subscribers</span>
            <span>${book.subscriber_count || 0}</span>
            
            <span style="color:var(--text-muted);">Tags</span>
            <span>${(book.tags || []).map(t => `<span class="badge badge-pending" style="font-size:0.75rem;margin-right:4px;">${t}</span>`).join('') || '-'}</span>
            
            <span style="color:var(--text-muted);">Version</span>
            <span>v${book.version}</span>
          </div>
          
          <div style="margin-top:16px;">
            <span style="color:var(--text-muted);">Description</span>
            <p style="margin-top:4px;color:var(--text-primary);">${book.description || 'No description'}</p>
          </div>
        </div>
      </div>
    `;

        // Load reviews - also store embedded reviews for display
        window.currentBookId = book.id;
        // If book has embedded reviews, display them directly; otherwise fetch
        if (book.reviews && book.reviews.length > 0) {
            displayBookReviews(book.id, book.reviews);
        } else {
            loadBookReviews(book.id);
        }
    } catch (e) {
        toast(e.message || 'Failed to load book', 'error');
    }
}

async function loadBookReviews(bookId) {
    try {
        const data = await libraryApi.getBookReviews(bookId);
        // API returns array directly, not {items: []}
        const reviews = Array.isArray(data) ? data : (data.items || []);
        displayBookReviews(bookId, reviews);
    } catch (e) {
        document.getElementById('bookReviewsList').innerHTML = `<span class="text-error">Error loading reviews: ${e.message}</span>`;
    }
}

function displayBookReviews(bookId, reviews) {
    const container = document.getElementById('bookReviewsList');

    if (!reviews || reviews.length === 0) {
        container.innerHTML = '<div class="text-muted">No reviews yet</div>';
        return;
    }

    container.innerHTML = reviews.map(r => `
      <div class="card" style="margin-bottom:12px;background:var(--bg-tertiary);padding:16px;border-radius:8px;">
        <div class="flex justify-between items-center">
          <strong style="color:var(--warning);">${'⭐'.repeat(r.rating)}</strong>
          <span class="text-muted text-sm">${new Date(r.created_at).toLocaleDateString()}</span>
        </div>
        <p class="mt-2" style="color:var(--text-primary);">${r.comment || '<em>No comment</em>'}</p>
        <div class="mt-2 text-muted text-sm">
          👍 ${r.helpful_count || 0} | 👎 ${r.unhelpful_count || 0}
          ${isLoggedIn() ? `
            <button class="btn btn-sm btn-secondary" onclick="voteOnReview(${r.id}, 'HELPFUL')">👍</button>
            <button class="btn btn-sm btn-secondary" onclick="voteOnReview(${r.id}, 'UNHELPFUL')">👎</button>
          ` : ''}
        </div>
      </div>
    `).join('');
}

async function submitReview() {
    const bookId = window.currentBookId;
    if (!bookId) return toast('No book selected', 'error');

    const rating = parseInt(document.getElementById('reviewRating').value);
    const comment = document.getElementById('reviewComment').value || null;

    try {
        await libraryApi.createReview(bookId, rating, comment);
        toast('Review submitted!', 'success');
        loadBookReviews(bookId);
    } catch (e) {
        toast(e.message || 'Failed to submit review', 'error');
    }
}

async function voteOnReview(reviewId, vote) {
    try {
        await libraryApi.voteReview(reviewId, vote);
        toast('Vote recorded!', 'success');
        // Reload reviews for current book
        if (window.currentBookId) {
            loadBookReviews(window.currentBookId);
        }
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
    }
}

// ============================================
// BOOK CRUD HANDLERS
// ============================================
function showBookTab(tab) {
    document.querySelectorAll('#page-book-crud .tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('#page-book-crud .tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`#page-book-crud .tab-btn[onclick="showBookTab('${tab}')"]`).classList.add('active');
    document.getElementById(`bookTab-${tab}`).classList.add('active');

    if (tab === 'my') loadMyBooks();
}

async function loadMyBooks() {
    if (!isLoggedIn()) {
        document.getElementById('myBooksDisplay').innerHTML = '<span class="text-muted">Login to view your books</span>';
        return;
    }

    try {
        const data = await libraryApi.getMyBooks();
        if (!data.items || data.items.length === 0) {
            document.getElementById('myBooksDisplay').innerHTML = '<span class="text-muted">No books found</span>';
            return;
        }

        let html = '<table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Version</th></tr></thead><tbody>';
        data.items.forEach(b => {
            html += `<tr onclick="editBook(${b.id})" style="cursor:pointer">
        <td>${b.id}</td>
        <td>${b.title}</td>
        <td><span class="badge badge-${b.status?.toLowerCase()}">${b.status}</span></td>
        <td>${b.version}</td>
      </tr>`;
        });
        html += '</tbody></table>';
        document.getElementById('myBooksDisplay').innerHTML = html;
    } catch (e) {
        document.getElementById('myBooksDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

function editBook(id) {
    document.getElementById('editBookId').value = id;
    showBookTab('edit');
    loadBookForEdit();
}

async function handleCreateBook() {
    const title = document.getElementById('createBookTitle').value;
    const year = document.getElementById('createBookYear').value;
    const tags = document.getElementById('createBookTags').value;
    const desc = document.getElementById('createBookDesc').value;
    const authors = document.getElementById('createBookAuthors').value;

    if (!title) return toast('Title is required', 'error');

    const data = { title };
    if (year) data.year = parseInt(year);
    if (tags) data.tags = tags.split(',').map(t => t.trim());
    if (desc) data.description = desc;
    if (authors) data.author_ids = authors.split(',').map(a => parseInt(a.trim()));

    try {
        const result = await libraryApi.createBook(data);
        toast(`Book created with ID: ${result.id}`, 'success');
    } catch (e) {
        toast(e.message || 'Create failed', 'error');
    }
}

async function loadBookForEdit() {
    const id = document.getElementById('editBookId').value;
    if (!id) return;

    try {
        const book = await libraryApi.getBook(id);
        document.getElementById('editBookForm').classList.remove('hidden');
        document.getElementById('editBookTitle').value = book.title || '';
        document.getElementById('editBookYear').value = book.year || '';
        document.getElementById('editBookVersion').value = book.version;
        document.getElementById('editBookDesc').value = book.description || '';
        document.getElementById('editBookTags').value = (book.tags || []).join(', ');
        window.editBookData = book;
    } catch (e) {
        toast(e.message || 'Failed to load book', 'error');
    }
}

async function handleUpdateBook() {
    const id = document.getElementById('editBookId').value;
    const version = parseInt(document.getElementById('editBookVersion').value);

    const data = {
        version,
        title: document.getElementById('editBookTitle').value,
        year: parseInt(document.getElementById('editBookYear').value) || null,
        description: document.getElementById('editBookDesc').value || null,
        tags: document.getElementById('editBookTags').value.split(',').map(t => t.trim()).filter(t => t),
    };

    try {
        await libraryApi.updateBook(id, data);
        toast('Book updated!', 'success');
        loadBookForEdit();
    } catch (e) {
        toast(e.message || 'Update failed', 'error');
    }
}

async function handleDeleteBook() {
    const id = document.getElementById('editBookId').value;
    if (!confirm('Are you sure you want to delete this book?')) return;

    try {
        await libraryApi.deleteBook(id);
        toast('Book deleted', 'success');
        document.getElementById('editBookForm').classList.add('hidden');
    } catch (e) {
        toast(e.message || 'Delete failed', 'error');
    }
}

// ============================================
// AUTHORS HANDLERS
// ============================================
async function searchAuthors() {
    const search = document.getElementById('authorSearch').value;

    try {
        const data = await libraryApi.listAuthors({ search, limit: 20 });
        renderAuthors(data.items || []);
    } catch (e) {
        toast(e.message || 'Search failed', 'error');
    }
}

function renderAuthors(authors) {
    const container = document.getElementById('authorsResults');
    if (authors.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="icon">✍️</div>No authors found</div>';
        return;
    }

    container.innerHTML = authors.map(a => {
        // AuthorRead has avatar_key but not avatar_urls computed field
        // Show a placeholder or construct URL if we had S3 config
        const hasAvatar = !!a.avatar_key;
        const avatarHtml = hasAvatar
            ? `<div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;flex-shrink:0;">${a.name.charAt(0).toUpperCase()}</div>`
            : `<div style="width:48px;height:48px;border-radius:50%;background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center;color:var(--text-muted);flex-shrink:0;">👤</div>`;

        return `
    <div class="item-card" onclick="viewAuthor(${a.id})" style="display:flex;align-items:center;gap:16px;">
      ${avatarHtml}
      <div style="flex:1;">
        <div class="item-card-title">${a.name}</div>
        <div class="item-card-meta">
          <span class="badge badge-${a.status?.toLowerCase()}">${a.status}</span>
          <span style="margin-left:8px;">👥 ${a.follower_count || 0}</span>
        </div>
      </div>
    </div>
  `;
    }).join('');
}

function viewAuthor(id) {
    document.getElementById('authorDetailId').value = id;
    navigateTo('author-detail');
    loadAuthorDetail();
}

// ============================================
// AUTHOR DETAIL PAGE
// ============================================
async function loadAuthorDetail() {
    const id = document.getElementById('authorDetailId').value;
    if (!id) return;

    try {
        const author = await libraryApi.getAuthor(id);
        document.getElementById('authorDetailDisplay').classList.remove('hidden');
        document.getElementById('authorBooksSection').classList.remove('hidden');
        window.currentAuthorId = author.id;

        // Build avatar HTML - AuthorDetail has avatar_urls from AvatarKeyMixin
        const avatarUrl = author.avatar_urls?.['256'] || author.avatar_urls?.['128'] || author.avatar_urls?.['512'] || null;
        const avatarHtml = avatarUrl
            ? `<img src="${avatarUrl}" alt="${author.name}" style="width:120px;height:120px;border-radius:50%;object-fit:cover;box-shadow:0 8px 24px rgba(0,0,0,0.3);">`
            : `<div style="width:120px;height:120px;border-radius:50%;background:linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);display:flex;align-items:center;justify-content:center;color:white;font-size:3rem;font-weight:bold;">${author.name.charAt(0).toUpperCase()}</div>`;

        // Follow button (if logged in)
        const followHtml = isLoggedIn()
            ? `<button class="btn btn-secondary" onclick="toggleFollowAuthor(${author.id})" style="margin-top:12px;">👥 Follow</button>`
            : '';

        document.getElementById('authorDetailContent').innerHTML = `
      <div style="display:flex;gap:24px;align-items:flex-start;">
        <div style="flex-shrink:0;text-align:center;">
          ${avatarHtml}
          ${followHtml}
        </div>
        <div style="flex:1;">
          <h3 style="margin:0;font-size:1.5rem;color:var(--text-primary);">${author.name}</h3>
          <span class="badge badge-${author.status?.toLowerCase() || 'pending'}" style="margin-top:8px;display:inline-block;">${author.status}</span>
          
          <div style="margin-top:16px;display:grid;grid-template-columns:auto 1fr;gap:8px 16px;color:var(--text-secondary);">
            <span style="color:var(--text-muted);">Email</span>
            <span>${author.email || '-'}</span>
            
            <span style="color:var(--text-muted);">Followers</span>
            <span>👥 ${author.follower_count || 0}</span>
            
            <span style="color:var(--text-muted);">Books</span>
            <span>📚 ${author.books?.length || 0}</span>
            
            <span style="color:var(--text-muted);">Version</span>
            <span>v${author.version}</span>
          </div>
          
          <div style="margin-top:16px;">
            <span style="color:var(--text-muted);">Bio</span>
            <p style="margin-top:4px;color:var(--text-primary);">${author.bio || 'No biography'}</p>
          </div>
        </div>
      </div>
    `;

        // Display books by this author
        if (author.books && author.books.length > 0) {
            document.getElementById('authorBooksList').innerHTML = author.books.map(b => `
        <div class="item-card" onclick="navigateTo('book-detail'); document.getElementById('bookDetailId').value='${b.id}'; loadBookDetail();">
          <div class="item-card-title">${b.title}</div>
          <div class="item-card-meta">
            <span class="badge badge-${b.status?.toLowerCase()}">${b.status}</span>
            <span style="margin-left:8px;">⭐ ${b.average_rating?.toFixed(1) || '0.0'}</span>
          </div>
        </div>
      `).join('');
        } else {
            document.getElementById('authorBooksList').innerHTML = '<div class="text-muted">No books found</div>';
        }
    } catch (e) {
        toast(e.message || 'Failed to load author', 'error');
    }
}

async function toggleFollowAuthor(authorId) {
    try {
        await libraryApi.followAuthor(authorId);
        toast('Following author!', 'success');
        loadAuthorDetail();
    } catch (e) {
        // Might already be following, try unfollow
        try {
            await libraryApi.unfollowAuthor(authorId);
            toast('Unfollowed author', 'info');
            loadAuthorDetail();
        } catch (e2) {
            toast(e.message || 'Action failed', 'error');
        }
    }
}

// ============================================
// AUTHOR CRUD HANDLERS
// ============================================
function showAuthorTab(tab) {
    document.querySelectorAll('#page-author-crud .tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('#page-author-crud .tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`#page-author-crud .tab-btn[onclick="showAuthorTab('${tab}')"]`).classList.add('active');
    document.getElementById(`authorTab-${tab}`).classList.add('active');

    if (tab === 'my') loadMyAuthors();
}

async function loadMyAuthors() {
    if (!isLoggedIn()) {
        document.getElementById('myAuthorsDisplay').innerHTML = '<span class="text-muted">Login to view your authors</span>';
        return;
    }

    try {
        const data = await libraryApi.getMyAuthors();
        if (!data.items || data.items.length === 0) {
            document.getElementById('myAuthorsDisplay').innerHTML = '<span class="text-muted">No authors found</span>';
            return;
        }

        let html = '<table><thead><tr><th>ID</th><th>Name</th><th>Status</th><th>Followers</th></tr></thead><tbody>';
        data.items.forEach(a => {
            html += `<tr onclick="editAuthor(${a.id})" style="cursor:pointer">
        <td>${a.id}</td>
        <td>${a.name}</td>
        <td><span class="badge badge-${a.status?.toLowerCase()}">${a.status}</span></td>
        <td>${a.follower_count || 0}</td>
      </tr>`;
        });
        html += '</tbody></table>';
        document.getElementById('myAuthorsDisplay').innerHTML = html;
    } catch (e) {
        document.getElementById('myAuthorsDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

function editAuthor(id) {
    document.getElementById('editAuthorId').value = id;
    showAuthorTab('edit');
    loadAuthorForEdit();
}

async function handleCreateAuthor() {
    const name = document.getElementById('createAuthorName').value;
    const email = document.getElementById('createAuthorEmail').value;
    const bio = document.getElementById('createAuthorBio').value;
    const userId = document.getElementById('createAuthorUserId').value;

    if (!name) return toast('Name is required', 'error');

    const data = { name };
    if (email) data.email = email;
    if (bio) data.bio = bio;
    if (userId) data.linked_user_id = userId;

    try {
        const result = await libraryApi.createAuthor(data);
        toast(`Author created with ID: ${result.id}`, 'success');
    } catch (e) {
        toast(e.message || 'Create failed', 'error');
    }
}

async function loadAuthorForEdit() {
    const id = document.getElementById('editAuthorId').value;
    if (!id) return;

    try {
        const author = await libraryApi.getAuthor(id);
        document.getElementById('editAuthorForm').classList.remove('hidden');
        document.getElementById('editAuthorName').value = author.name || '';
        document.getElementById('editAuthorEmail').value = author.email || '';
        document.getElementById('editAuthorBio').value = author.bio || '';
        document.getElementById('editAuthorVersion').value = author.version;
        window.editAuthorData = author;
    } catch (e) {
        toast(e.message || 'Failed to load author', 'error');
    }
}

async function handleUpdateAuthor() {
    const id = document.getElementById('editAuthorId').value;
    const version = parseInt(document.getElementById('editAuthorVersion').value);

    const data = {
        version,
        name: document.getElementById('editAuthorName').value,
        email: document.getElementById('editAuthorEmail').value || null,
        bio: document.getElementById('editAuthorBio').value || null,
    };

    try {
        await libraryApi.updateAuthor(id, data);
        toast('Author updated!', 'success');
        loadAuthorForEdit();
    } catch (e) {
        toast(e.message || 'Update failed', 'error');
    }
}

async function handleDeleteAuthor() {
    const id = document.getElementById('editAuthorId').value;
    if (!confirm('Are you sure you want to delete this author?')) return;

    try {
        await libraryApi.deleteAuthor(id);
        toast('Author deleted', 'success');
        document.getElementById('editAuthorForm').classList.add('hidden');
    } catch (e) {
        toast(e.message || 'Delete failed', 'error');
    }
}

// ============================================
// COLLECTIONS HANDLERS
// ============================================
async function searchCollections() {
    const q = document.getElementById('collectionSearch').value;
    const sort = document.getElementById('collectionSort').value;

    const params = { limit: 20 };
    if (q) params.q = q;
    if (sort) params.sort = [sort];

    try {
        const data = await libraryApi.listCollections(params);
        renderCollections(data.items || []);
    } catch (e) {
        toast(e.message || 'Search failed', 'error');
    }
}

function renderCollections(collections) {
    const container = document.getElementById('collectionsResults');
    if (collections.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="icon">📚</div>No collections found</div>';
        return;
    }

    container.innerHTML = collections.map(c => {
        const hasCover = !!c.cover_urls;
        const coverHtml = hasCover
            ? `<img src="${c.cover_urls['600x900']}" style="width:48px;height:72px;border-radius:8px;object-fit:cover;flex-shrink:0;" alt="Cover">`
            : `<div style="width:48px;height:72px;border-radius:8px;background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center;color:var(--text-muted);flex-shrink:0;">📚</div>`;

        return `
    <div class="item-card" onclick="viewCollectionDetail(${c.id})" style="display:flex;align-items:center;gap:16px;cursor:pointer;">
      ${coverHtml}
      <div style="flex:1;">
        <div class="item-card-title">${c.name}</div>
        <div class="item-card-meta">
          <span class="badge badge-${c.status?.toLowerCase()}">${c.status}</span>
          <span style="margin-left:8px;">📖 ${c.book_count || 0}</span>
          <span style="margin-left:8px;">👥 ${c.subscriber_count || 0}</span>
        </div>
      </div>
    </div>
  `;
    }).join('');
}

async function handleCreateCollection() {
    const name = document.getElementById('createCollectionName').value;
    const desc = document.getElementById('createCollectionDesc').value;
    const books = document.getElementById('createCollectionBooks').value;

    if (!name) return toast('Name is required', 'error');

    const data = { name };
    if (desc) data.description = desc;
    if (books) data.book_ids = books.split(',').map(b => parseInt(b.trim()));

    try {
        const result = await libraryApi.createCollection(data);
        toast(`Collection created with ID: ${result.id}`, 'success');
        searchCollections();
    } catch (e) {
        toast(e.message || 'Create failed', 'error');
    }
}

// Collection detail view
async function viewCollectionDetail(id) {
    try {
        const collection = await libraryApi.getCollection(id);
        window.currentCollection = collection;

        const booksHtml = (collection.books || []).length > 0
            ? collection.books.map(b => `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:8px;background:var(--bg-tertiary);border-radius:6px;margin-bottom:8px;">
                    <span><strong>#${b.position}</strong> - ${b.book?.title || 'Book #' + b.book_id}</span>
                    ${isLoggedIn() ? `<button class="btn btn-sm btn-danger" onclick="removeBookFromCol(${collection.id}, ${b.book_id})">Remove</button>` : ''}
                </div>
            `).join('')
            : '<div class="text-muted">No books in this collection</div>';

        const html = `
            <div class="card" style="margin-bottom:16px;">
                <h3>${collection.name}</h3>
                <span class="badge badge-${collection.status?.toLowerCase()}">${collection.status}</span>
                <p style="margin-top:12px;color:var(--text-secondary);">${collection.description || 'No description'}</p>
                <div style="margin-top:12px;color:var(--text-muted);">
                    📚 ${collection.book_count || 0} books | 👥 ${collection.subscriber_count || 0} subscribers | 👁 ${collection.view_count || 0} views | v${collection.version}
                </div>
            </div>
            <h4>Books in Collection</h4>
            ${booksHtml}
            ${isLoggedIn() ? `
            <div class="card" style="margin-top:16px;">
                <h4>Add Book to Collection</h4>
                <div class="form-group">
                    <input type="number" id="addBookToColId" placeholder="Book ID">
                    <input type="number" id="addBookToColPosition" placeholder="Position (optional)">
                </div>
                <button class="btn btn-primary" onclick="addBookToCol(${collection.id})">Add Book</button>
            </div>
            ` : ''}
        `;

        document.getElementById('collectionDetailContent').innerHTML = html;
        document.getElementById('collectionDetailSection').classList.remove('hidden');
    } catch (e) {
        toast(e.message || 'Failed to load collection', 'error');
    }
}

// ============================================
// COLLECTION DETAIL PAGE (dedicated page)
// ============================================
async function loadCollectionDetailPage() {
    const id = document.getElementById('collectionDetailId').value;
    if (!id) return;

    try {
        const collection = await libraryApi.getCollection(id);
        document.getElementById('collectionDetailPageDisplay').classList.remove('hidden');
        document.getElementById('collectionBooksSection').classList.remove('hidden');
        window.currentCollectionId = collection.id;
        window.currentCollection = collection; // Store full collection for reordering

        // Build cover HTML
        const hasCover = !!collection.cover_urls;
        const coverHtml = hasCover
            ? `<img src="${collection.cover_urls['600x900']}" style="width:120px;height:180px;border-radius:12px;object-fit:cover;" alt="Cover">`
            : `<div style="width:120px;height:180px;border-radius:12px;background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:3rem;">📚</div>`;

        document.getElementById('collectionDetailPageContent').innerHTML = `
      <div style="display:flex;gap:24px;align-items:flex-start;">
        <div style="flex-shrink:0;">
          ${coverHtml}
        </div>
        <div style="flex:1;">
          <h3 style="margin:0;font-size:1.5rem;color:var(--text-primary);">${collection.name}</h3>
          <span class="badge badge-${collection.status?.toLowerCase() || 'pending'}" style="margin-top:8px;display:inline-block;">${collection.status}</span>
          
          <div style="margin-top:16px;display:grid;grid-template-columns:auto 1fr;gap:8px 16px;color:var(--text-secondary);">
            <span style="color:var(--text-muted);">Books</span>
            <span>📚 ${collection.book_count || 0}</span>
            
            <span style="color:var(--text-muted);">Subscribers</span>
            <span>👥 ${collection.subscriber_count || 0}</span>
            
            <span style="color:var(--text-muted);">Views</span>
            <span>👁 ${collection.view_count || 0}</span>
            
            <span style="color:var(--text-muted);">Version</span>
            <span>v${collection.version}</span>
          </div>
          
          <div style="margin-top:16px;">
            <span style="color:var(--text-muted);">Description</span>
            <p style="margin-top:4px;color:var(--text-primary);">${collection.description || 'No description'}</p>
          </div>
        </div>
      </div>
    `;

        // Display books in collection with up/down reorder buttons
        if (collection.books && collection.books.length > 0) {
            // Sort by position
            const sortedBooks = [...collection.books].sort((a, b) => a.position - b.position);
            window.currentCollectionBooks = sortedBooks; // Store for reordering

            document.getElementById('collectionBooksList').innerHTML = sortedBooks.map((b, idx) => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px;background:var(--bg-tertiary);border-radius:8px;margin-bottom:8px;" data-book-id="${b.book_id}">
          <div style="display:flex;align-items:center;gap:12px;">
            <span style="font-size:1.25rem;font-weight:bold;color:var(--primary);">#${idx + 1}</span>
            <div>
              <div style="font-weight:bold;color:var(--text-primary);">${b.book?.title || 'Book #' + b.book_id}</div>
              <div class="text-muted text-sm">⭐ ${b.book?.average_rating?.toFixed(1) || '0.0'} | 👥 ${b.book?.subscriber_count || 0}</div>
            </div>
          </div>
          <div style="display:flex;gap:4px;">
            ${isLoggedIn() ? `
              <button class="btn btn-sm btn-secondary" onclick="moveBookInCollection(${idx}, -1)" ${idx === 0 ? 'disabled' : ''}>⬆</button>
              <button class="btn btn-sm btn-secondary" onclick="moveBookInCollection(${idx}, 1)" ${idx === sortedBooks.length - 1 ? 'disabled' : ''}>⬇</button>
              <button class="btn btn-sm btn-danger" onclick="removeBookFromCollection(${collection.id}, ${b.book_id})">Remove</button>
            ` : ''}
          </div>
        </div>
      `).join('');
        } else {
            document.getElementById('collectionBooksList').innerHTML = '<div class="text-muted">No books in this collection</div>';
        }

        // Show book management if logged in
        if (isLoggedIn()) {
            document.getElementById('collectionBookManagement').style.display = 'block';
        }
    } catch (e) {
        toast(e.message || 'Failed to load collection', 'error');
    }
}

async function handleAddBookToCollection() {
    const collectionId = window.currentCollectionId;
    const bookId = document.getElementById('addBookId').value;
    const position = document.getElementById('addBookPosition').value;

    if (!collectionId) return toast('No collection selected', 'error');
    if (!bookId) return toast('Book ID required', 'error');

    try {
        await libraryApi.addBookToCollection(collectionId, parseInt(bookId), position ? parseInt(position) : null);
        toast('Book added to collection!', 'success');
        loadCollectionDetailPage();
        document.getElementById('addBookId').value = '';
        document.getElementById('addBookPosition').value = '';
    } catch (e) {
        toast(e.message || 'Failed to add book', 'error');
    }
}

async function removeBookFromCollection(collectionId, bookId) {
    if (!confirm('Remove this book from collection?')) return;

    const collection = window.currentCollection;
    const books = window.currentCollectionBooks;

    if (!collection || !books) return toast('No collection loaded', 'error');

    // Filter out the book to remove and get remaining book_ids
    const remainingBookIds = books
        .filter(b => b.book_id !== bookId)
        .map(b => b.book_id);

    try {
        // Use PATCH with remaining book_ids array
        await libraryApi.updateCollection(collection.id, { book_ids: remainingBookIds, version: collection.version });
        toast('Book removed!', 'success');
        loadCollectionDetailPage();
    } catch (e) {
        toast(e.message || 'Failed to remove book', 'error');
    }
}

async function moveBookInCollection(currentIndex, direction) {
    const collection = window.currentCollection;
    const books = window.currentCollectionBooks;

    if (!collection || !books) return toast('No collection loaded', 'error');

    const newIndex = currentIndex + direction;
    if (newIndex < 0 || newIndex >= books.length) return;

    // Swap positions in array
    const newOrder = [...books];
    [newOrder[currentIndex], newOrder[newIndex]] = [newOrder[newIndex], newOrder[currentIndex]];

    // Extract book_ids in new order
    const bookIds = newOrder.map(b => b.book_id);

    try {
        // Use PATCH /collections/{id} with book_ids array to reorder
        await libraryApi.updateCollection(collection.id, { book_ids: bookIds, version: collection.version });
        toast('Order updated!', 'success');
        loadCollectionDetailPage();
    } catch (e) {
        toast(e.message || 'Failed to update order', 'error');
    }
}

async function addBookToCol(collectionId) {
    const bookId = document.getElementById('addBookToColId').value;
    const position = document.getElementById('addBookToColPosition').value;

    if (!bookId) return toast('Book ID required', 'error');

    try {
        await libraryApi.addBookToCollection(collectionId, parseInt(bookId), position ? parseInt(position) : null);
        toast('Book added!', 'success');
        viewCollectionDetail(collectionId);
    } catch (e) {
        toast(e.message || 'Failed to add book', 'error');
    }
}

async function removeBookFromCol(collectionId, bookId) {
    if (!confirm('Remove this book from collection?')) return;

    try {
        await libraryApi.removeBookFromCollection(collectionId, bookId);
        toast('Book removed!', 'success');
        viewCollectionDetail(collectionId);
    } catch (e) {
        toast(e.message || 'Failed to remove book', 'error');
    }
}

// ============================================
// JURY HANDLERS
// ============================================
function showJuryTab(tab) {
    document.querySelectorAll('#page-jury .tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('#page-jury .tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`#page-jury .tab-btn[onclick="showJuryTab('${tab}')"]`).classList.add('active');
    document.getElementById(`juryTab-${tab}`).classList.add('active');

    if (tab === 'authors') loadPendingAuthors();
    else if (tab === 'books') loadPendingBooks();
    else if (tab === 'collections') loadPendingCollections();
}

async function loadPendingAuthors() {
    try {
        const data = await libraryApi.getPendingAuthors();
        renderPendingItems('authors', data.items || [], 'pendingAuthorsDisplay');
    } catch (e) {
        document.getElementById('pendingAuthorsDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

async function loadPendingBooks() {
    try {
        const data = await libraryApi.getPendingBooks();
        renderPendingItems('books', data.items || [], 'pendingBooksDisplay');
    } catch (e) {
        document.getElementById('pendingBooksDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

async function loadPendingCollections() {
    try {
        const data = await libraryApi.getPendingCollections();
        renderPendingItems('collections', data.items || [], 'pendingCollectionsDisplay');
    } catch (e) {
        document.getElementById('pendingCollectionsDisplay').innerHTML = `<span class="text-error">Error: ${e.message}</span>`;
    }
}

function renderPendingItems(type, items, containerId) {
    const container = document.getElementById(containerId);
    if (items.length === 0) {
        container.innerHTML = '<span class="text-muted">No pending items</span>';
        return;
    }

    // Check if user has curator scope (jury:override)
    const isCurator = state.scopes && state.scopes.includes('jury:override');
    // Check if user can vote
    const canVote = state.scopes && (state.scopes.includes('jury:vote') || state.scopes.includes('jury:vote_weighted'));
    // Check vote weight (5 if trusted, 1 otherwise)
    const voteWeight = state.scopes && state.scopes.includes('jury:vote_weighted') ? 5 : 1;

    let html = '<table><thead><tr><th>ID</th><th>Name/Title</th><th>Vote Score</th><th>Action</th></tr></thead><tbody>';
    items.forEach(item => {
        const name = item.name || item.title;
        const voteScore = item.vote_score || 0;
        const threshold = 5;
        const progress = Math.min(100, (voteScore / threshold) * 100);

        // Vote score display with progress bar
        const scoreHtml = `
            <div style="min-width:80px;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-weight:600;color:var(--primary);">${voteScore}</span>
                    <span style="color:var(--text-muted);">/ ${threshold}</span>
                </div>
                <div style="height:4px;background:var(--bg-tertiary);border-radius:2px;margin-top:4px;">
                    <div style="height:100%;width:${progress}%;background:linear-gradient(90deg,var(--primary),var(--secondary));border-radius:2px;"></div>
                </div>
            </div>
        `;

        // Action buttons
        let actionHtml = '';
        if (canVote) {
            actionHtml += `<button class="btn btn-sm btn-primary" onclick="voteOn${capitalize(type)}(${item.id})" title="Your vote weight: +${voteWeight}">+${voteWeight} Vote</button>`;
        }
        if (isCurator) {
            actionHtml += `
                <button class="btn btn-sm btn-success" onclick="curatorApprove${capitalize(type)}(${item.id})" style="margin-left:4px;" title="Curator instant approve">✓ Approve</button>
                <button class="btn btn-sm btn-danger" onclick="curatorReject${capitalize(type)}(${item.id})" style="margin-left:4px;" title="Curator instant reject">✗ Reject</button>
            `;
        }
        if (!canVote && !isCurator) {
            actionHtml = '<span class="text-muted">No permission</span>';
        }

        html += `<tr>
            <td>${item.id}</td>
            <td>${name}</td>
            <td>${scoreHtml}</td>
            <td style="white-space:nowrap;">${actionHtml}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1, -1); }

async function voteOnAuthor(id) {
    try {
        const result = await libraryApi.voteOnAuthor(id);
        toast(`Vote cast! (+${result.vote_weight}) New score: ${result.new_vote_score}${result.auto_approved ? ' - AUTO APPROVED!' : ''}`, 'success');
        loadPendingAuthors();
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
    }
}

async function voteOnBook(id) {
    try {
        const result = await libraryApi.voteOnBook(id);
        toast(`Vote cast! (+${result.vote_weight}) New score: ${result.new_vote_score}${result.auto_approved ? ' - AUTO APPROVED!' : ''}`, 'success');
        loadPendingBooks();
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
    }
}

async function voteOnCollection(id) {
    try {
        const result = await libraryApi.voteOnCollection(id);
        toast(`Vote cast! (+${result.vote_weight}) New score: ${result.new_vote_score}${result.auto_approved ? ' - AUTO APPROVED!' : ''}`, 'success');
        loadPendingCollections();
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
    }
}

// Curator approve/reject functions
async function curatorApproveAuthor(id) {
    if (!confirm('Approve this author immediately?')) return;
    try {
        await libraryApi.approveAuthor(id);
        toast('Author approved!', 'success');
        loadPendingAuthors();
    } catch (e) {
        toast(e.message || 'Approve failed', 'error');
    }
}

async function curatorRejectAuthor(id) {
    const reason = prompt('Rejection reason:');
    if (!reason) return;
    try {
        await libraryApi.rejectAuthor(id, reason);
        toast('Author rejected', 'info');
        loadPendingAuthors();
    } catch (e) {
        toast(e.message || 'Reject failed', 'error');
    }
}

async function curatorApproveBook(id) {
    if (!confirm('Approve this book immediately?')) return;
    try {
        await libraryApi.approveBook(id);
        toast('Book approved!', 'success');
        loadPendingBooks();
    } catch (e) {
        toast(e.message || 'Approve failed', 'error');
    }
}

async function curatorRejectBook(id) {
    const reason = prompt('Rejection reason:');
    if (!reason) return;
    try {
        await libraryApi.rejectBook(id, reason);
        toast('Book rejected', 'info');
        loadPendingBooks();
    } catch (e) {
        toast(e.message || 'Reject failed', 'error');
    }
}

async function curatorApproveCollection(id) {
    if (!confirm('Approve this collection immediately?')) return;
    try {
        await libraryApi.approveCollection(id);
        toast('Collection approved!', 'success');
        loadPendingCollections();
    } catch (e) {
        toast(e.message || 'Approve failed', 'error');
    }
}

async function curatorRejectCollection(id) {
    const reason = prompt('Rejection reason:');
    if (!reason) return;
    try {
        await libraryApi.rejectCollection(id, reason);
        toast('Collection rejected', 'info');
        loadPendingCollections();
    } catch (e) {
        toast(e.message || 'Reject failed', 'error');
    }
}

// ============================================
// HISTORY HANDLERS
// ============================================
async function loadHistory() {
    const type = document.getElementById('historyEntityType').value;
    const id = document.getElementById('historyEntityId').value;
    if (!id) return toast('Enter entity ID', 'error');

    try {
        let data;
        if (type === 'book') data = await libraryApi.getBookHistory(id);
        else if (type === 'author') data = await libraryApi.getAuthorHistory(id);
        else data = await libraryApi.getCollectionHistory(id);

        window.historyEntityType = type;
        window.historyEntityId = id;
        renderHistory(data.items || []);
    } catch (e) {
        toast(e.message || 'Failed to load history', 'error');
    }
}

function renderHistory(items) {
    const container = document.getElementById('historyTimeline');
    if (items.length === 0) {
        container.innerHTML = '<div class="text-muted">No history found</div>';
        return;
    }

    container.innerHTML = `<div class="timeline">
    ${items.map(h => `
      <div class="timeline-item action-${h.action}" onclick="viewHistoryDetail(${h.id})">
        <div class="flex justify-between">
          <span class="badge badge-info">${h.action}</span>
          <span class="text-muted text-sm">v${h.version}</span>
        </div>
        <div class="mt-2 text-sm">${new Date(h.created_at).toLocaleString()}</div>
        <div class="text-muted text-sm">By: ${h.user_id?.substring(0, 8)}...</div>
      </div>
    `).join('')}
  </div>`;
}

async function viewHistoryDetail(historyId) {
    try {
        const detail = await libraryApi.getHistoryDetail(historyId);
        window.currentHistoryDetail = detail;

        document.getElementById('historyDetailSection').classList.remove('hidden');
        document.getElementById('historyDetailContent').innerHTML = `
      <div class="mb-4">
        <strong>Action:</strong> ${detail.action} | 
        <strong>Version:</strong> ${detail.version} |
        <strong>Date:</strong> ${new Date(detail.created_at).toLocaleString()}
      </div>
      <div class="diff-viewer">
        <div class="diff-side">
          <h4>Old Data</h4>
          <pre>${JSON.stringify(detail.old_data, null, 2) || 'N/A'}</pre>
        </div>
        <div class="diff-side">
          <h4>New Data</h4>
          <pre>${JSON.stringify(detail.new_data, null, 2) || 'N/A'}</pre>
        </div>
      </div>
      <div class="mt-4">
        <strong>Changes:</strong>
        <pre>${JSON.stringify(detail.changes, null, 2) || 'N/A'}</pre>
      </div>
    `;
    } catch (e) {
        toast(e.message || 'Failed to load detail', 'error');
    }
}

async function handleRollback() {
    const detail = window.currentHistoryDetail;
    if (!detail) return toast('No version selected', 'error');
    if (!confirm(`Rollback to version ${detail.version}?`)) return;

    const type = window.historyEntityType;
    const id = window.historyEntityId;
    const targetVersion = detail.version;

    try {
        // First, fetch the current entity to get its version for optimistic locking
        let currentEntity;
        if (type === 'book') {
            currentEntity = await libraryApi.getBook(id);
        } else if (type === 'author') {
            currentEntity = await libraryApi.getAuthor(id);
        } else if (type === 'collection') {
            currentEntity = await libraryApi.getCollection(id);
        }

        if (!currentEntity || currentEntity.version === undefined) {
            return toast('Failed to get current entity version', 'error');
        }

        const currentVersion = currentEntity.version;

        // Now call rollback with both target_version and current version
        if (type === 'book') {
            await libraryApi.rollbackBook(id, targetVersion, currentVersion);
        } else if (type === 'author') {
            await libraryApi.rollbackAuthor(id, targetVersion, currentVersion);
        } else if (type === 'collection') {
            await libraryApi.rollbackCollection(id, targetVersion, currentVersion);
        }

        toast('Rollback successful!', 'success');
        loadHistory();
    } catch (e) {
        toast(e.message || 'Rollback failed', 'error');
    }
}

// ============================================
// UPLOAD HANDLERS
// ============================================
async function handleBookCoverUpload() {
    const bookId = document.getElementById('uploadBookCoverId').value;
    const file = document.getElementById('uploadBookCoverFile').files[0];

    if (!bookId || !file) return toast('Select book ID and file', 'error');

    try {
        // Presign
        const presign = await libraryApi.presignBookCover(bookId, file.type);

        // Upload to S3
        await uploadToS3(presign, file);

        // Commit with upload_id and s3_key
        await libraryApi.commitBookCover(bookId, presign.upload_id, presign.s3_key);

        toast('Cover uploaded! Processing...', 'success');
    } catch (e) {
        toast(e.message || 'Upload failed', 'error');
    }
}

async function handleBookFileUpload() {
    const bookId = document.getElementById('uploadBookFileId').value;
    const file = document.getElementById('uploadBookFileFile').files[0];

    if (!bookId || !file) return toast('Select book ID and file', 'error');

    try {
        // Presign with filename
        const presign = await libraryApi.presignBookFile(bookId, file.type, file.name);

        // Upload to S3
        await uploadToS3(presign, file);

        // Commit with upload_id and s3_key
        await libraryApi.commitBookFile(bookId, presign.upload_id, presign.s3_key);

        toast('File uploaded! Processing...', 'success');
    } catch (e) {
        toast(e.message || 'Upload failed', 'error');
    }
}

async function handleAuthorAvatarUpload() {
    const authorId = document.getElementById('uploadAuthorAvatarId').value;
    const file = document.getElementById('uploadAuthorAvatarFile').files[0];

    if (!authorId || !file) return toast('Select author ID and file', 'error');

    try {
        // Presign
        const presign = await libraryApi.presignAuthorAvatar(authorId, file.type);

        // Upload to S3
        await uploadToS3(presign, file);

        // Commit with upload_id and s3_key
        await libraryApi.commitAuthorAvatar(authorId, presign.upload_id, presign.s3_key);

        toast('Avatar uploaded! Processing...', 'success');
    } catch (e) {
        toast(e.message || 'Upload failed', 'error');
    }
}

async function handleCollectionCoverUpload() {
    const collectionId = document.getElementById('uploadCollectionCoverId').value;
    const file = document.getElementById('uploadCollectionCoverFile').files[0];

    if (!collectionId || !file) return toast('Select collection ID and file', 'error');

    try {
        // Presign
        const presign = await libraryApi.presignCollectionCover(collectionId, file.type);

        // Upload to S3
        await uploadToS3(presign, file);

        // Commit with upload_id and s3_key
        await libraryApi.commitCollectionCover(collectionId, presign.upload_id, presign.s3_key);

        toast('Cover uploaded! Processing...', 'success');
    } catch (e) {
        toast(e.message || 'Upload failed', 'error');
    }
}

// ============================================
// HEALTH HANDLERS
// ============================================
async function checkLibraryHealth() {
    try {
        const data = await libraryApi.health();
        document.getElementById('libraryHealthDisplay').innerHTML = `
      <span class="badge badge-success">Healthy</span>
      <pre class="mt-2">${JSON.stringify(data, null, 2)}</pre>
    `;
    } catch (e) {
        document.getElementById('libraryHealthDisplay').innerHTML = `
      <span class="badge badge-error">Unhealthy</span>
      <p class="text-error mt-2">${e.message}</p>
    `;
    }
}

async function checkAuthHealth() {
    try {
        const response = await fetch(CONFIG.AUTH_URL + '/ready');
        const data = await response.json();
        document.getElementById('authHealthDisplay').innerHTML = `
      <span class="badge badge-success">Healthy</span>
      <pre class="mt-2">${JSON.stringify(data, null, 2)}</pre>
    `;
    } catch (e) {
        document.getElementById('authHealthDisplay').innerHTML = `
      <span class="badge badge-error">Unhealthy</span>
      <p class="text-error mt-2">${e.message}</p>
    `;
    }
}

// ============================================
// PAGE HANDLERS (auto-load on navigation)
// ============================================
window.pageHandlers = {
    'profile': loadProfile,
    'sessions': loadSessions,
};
