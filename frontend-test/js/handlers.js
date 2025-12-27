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

        document.getElementById('bookDetailContent').innerHTML = `
      <h3>${book.title}</h3>
      <span class="badge badge-${book.status?.toLowerCase() || 'pending'}">${book.status}</span>
      <table class="mt-4" style="width:100%">
        <tr><td><strong>ID</strong></td><td>${book.id}</td></tr>
        <tr><td><strong>Year</strong></td><td>${book.year || '-'}</td></tr>
        <tr><td><strong>Version</strong></td><td>${book.version}</td></tr>
        <tr><td><strong>Rating</strong></td><td>${book.average_rating?.toFixed(1) || '-'} ⭐ (${book.review_count || 0} reviews)</td></tr>
        <tr><td><strong>Views</strong></td><td>${book.view_count || 0}</td></tr>
        <tr><td><strong>Subscribers</strong></td><td>${book.subscriber_count || 0}</td></tr>
        <tr><td><strong>Tags</strong></td><td>${(book.tags || []).join(', ') || '-'}</td></tr>
        <tr><td><strong>Description</strong></td><td>${book.description || '-'}</td></tr>
        <tr><td><strong>Authors</strong></td><td>${(book.authors || []).map(a => a.name).join(', ') || '-'}</td></tr>
      </table>
    `;

        // Load reviews
        window.currentBookId = book.id;
        loadBookReviews(book.id);
    } catch (e) {
        toast(e.message || 'Failed to load book', 'error');
    }
}

async function loadBookReviews(bookId) {
    try {
        const data = await libraryApi.getBookReviews(bookId);
        const container = document.getElementById('bookReviewsList');

        if (!data.items || data.items.length === 0) {
            container.innerHTML = '<div class="text-muted">No reviews yet</div>';
            return;
        }

        container.innerHTML = data.items.map(r => `
      <div class="card">
        <div class="flex justify-between items-center">
          <strong>${'⭐'.repeat(r.rating)}</strong>
          <span class="text-muted text-sm">${new Date(r.created_at).toLocaleDateString()}</span>
        </div>
        <p class="mt-2">${r.comment || '<em>No comment</em>'}</p>
        <div class="mt-2 text-muted text-sm">
          👍 ${r.helpful_count || 0} | 👎 ${r.unhelpful_count || 0}
          ${isLoggedIn() ? `
            <button class="btn btn-sm btn-secondary" onclick="voteOnReview(${bookId}, ${r.id}, 'HELPFUL')">👍</button>
            <button class="btn btn-sm btn-secondary" onclick="voteOnReview(${bookId}, ${r.id}, 'UNHELPFUL')">👎</button>
          ` : ''}
        </div>
      </div>
    `).join('');
    } catch (e) {
        document.getElementById('bookReviewsList').innerHTML = `<span class="text-error">Error loading reviews</span>`;
    }
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

async function voteOnReview(bookId, reviewId, vote) {
    try {
        await libraryApi.voteReview(bookId, reviewId, vote);
        toast('Vote recorded!', 'success');
        loadBookReviews(bookId);
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

    container.innerHTML = authors.map(a => `
    <div class="item-card" onclick="viewAuthor(${a.id})">
      <div class="item-card-title">${a.name}</div>
      <div class="item-card-meta">
        <span class="badge badge-${a.status?.toLowerCase()}">${a.status}</span>
        <br>Followers: ${a.follower_count || 0}
      </div>
    </div>
  `).join('');
}

function viewAuthor(id) {
    document.getElementById('editAuthorId').value = id;
    navigateTo('author-crud');
    showAuthorTab('edit');
    loadAuthorForEdit();
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

    container.innerHTML = collections.map(c => `
    <div class="item-card">
      <div class="item-card-title">${c.name}</div>
      <div class="item-card-meta">
        <span class="badge badge-${c.status?.toLowerCase()}">${c.status}</span>
        <br>Books: ${c.book_count || 0} | Views: ${c.view_count || 0}
      </div>
    </div>
  `).join('');
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

    let html = '<table><thead><tr><th>ID</th><th>Name/Title</th><th>Vote Score</th><th>Action</th></tr></thead><tbody>';
    items.forEach(item => {
        const name = item.name || item.title;
        html += `<tr>
      <td>${item.id}</td>
      <td>${name}</td>
      <td>${item.vote_score || 0}/5</td>
      <td><button class="btn btn-sm btn-primary" onclick="voteOn${capitalize(type)}(${item.id})">+1 Vote</button></td>
    </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1, -1); }

async function voteOnAuthor(id) {
    try {
        await libraryApi.voteOnAuthor(id);
        toast('Vote cast!', 'success');
        loadPendingAuthors();
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
    }
}

async function voteOnBook(id) {
    try {
        await libraryApi.voteOnBook(id);
        toast('Vote cast!', 'success');
        loadPendingBooks();
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
    }
}

async function voteOnCollection(id) {
    try {
        await libraryApi.voteOnCollection(id);
        toast('Vote cast!', 'success');
        loadPendingCollections();
    } catch (e) {
        toast(e.message || 'Vote failed', 'error');
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

    try {
        if (type === 'book') {
            await libraryApi.rollbackBook(id, detail.version);
        } else if (type === 'author') {
            await libraryApi.rollbackAuthor(id, detail.version);
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
