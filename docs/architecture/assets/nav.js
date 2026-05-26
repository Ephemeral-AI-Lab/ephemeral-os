(function () {
  const body = document.body;
  const root = body.dataset.root || '';
  const currentModule = body.dataset.module || '';
  const currentPage = body.dataset.page || (window.location.pathname.split('/').pop() || 'index');
  const currentFile = currentPage.endsWith('.html') ? currentPage : currentPage + '.html';

  function withRoot(path) {
    if (!path || path.startsWith('#') || /^(https?:|mailto:|tel:)/.test(path)) return path;
    if (path.startsWith('../') || path.startsWith('./')) return path;
    return root + path;
  }

  document.querySelectorAll('[data-doc-module]').forEach((link) => {
    if (!link.hasAttribute('data-doc-section') && link.dataset.docModule === currentModule) {
      link.classList.add('active');
    }
  });

  document.querySelectorAll('[data-doc-section]').forEach((link) => {
    const href = link.getAttribute('href') || '';
    const page = link.dataset.docPage || href.split('/').pop() || '';
    const module = link.dataset.docModule || currentModule;
    if (module === currentModule && page === currentFile) link.classList.add('active');
  });

  const active = document.querySelector('[data-doc-section].active');
  const activeLi = active ? active.closest('li') : null;
  if (activeLi && !activeLi.querySelector('ol.sub')) {
    const sections = Array.from(document.querySelectorAll('main section[id]'));
    const tocLinks = Array.from(document.querySelectorAll('.page-toc a[href^="#"]'));
    const links = tocLinks.length
      ? tocLinks.map((link) => {
          const href = link.getAttribute('href') || '';
          return { id: href.slice(1), text: link.textContent.replace(/\s+/g, ' ').trim() };
        })
      : sections.map((section) => {
          const heading = section.querySelector('h1, h2, h3');
          return {
            id: section.id,
            text: heading ? heading.textContent.replace(/[§#]/g, '').replace(/\s+/g, ' ').trim() : section.id
          };
        });
    if (links.length) {
      const sub = document.createElement('ol');
      sub.className = 'sub';
      for (const item of links.slice(0, 14)) {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = currentFile + '#' + item.id;
        a.textContent = item.text;
        li.appendChild(a);
        sub.appendChild(li);
      }
      activeLi.appendChild(sub);
    }
  }

  const input = document.querySelector('[data-doc-search]');
  const results = document.querySelector('[data-doc-search-results]');
  const index = window.ARCHITECTURE_DOC_SEARCH || [];
  if (!input || !results || !index.length) return;

  function render(query) {
    const q = query.trim().toLowerCase();
    results.innerHTML = '';
    if (!q) return;
    const matches = index.filter((item) => {
      const haystack = [item.module, item.title, item.text, item.id, item.tags].join(' ').toLowerCase();
      return haystack.includes(q);
    }).slice(0, 10);
    for (const item of matches) {
      const a = document.createElement('a');
      a.href = withRoot(item.href);
      a.innerHTML = '<span class="module-label">' + item.module + '</span><strong>' + item.title + '</strong><small>' + item.text + '</small>';
      results.appendChild(a);
    }
  }

  input.addEventListener('input', () => render(input.value));
})();
