(function () {
  const path = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('[data-doc-section]').forEach((link) => {
    const href = link.getAttribute('href') || '';
    if (href === path) link.classList.add('active');
  });

  const active = document.querySelector('[data-doc-section].active');
  const activeLi = active ? active.closest('li') : null;
  if (activeLi && !activeLi.querySelector('ol.sub')) {
    const sections = Array.from(document.querySelectorAll('main section[id]'));
    const tocLinks = Array.from(document.querySelectorAll('.page-toc a[href^="#"]'));
    const links = tocLinks.length
      ? tocLinks.map((link) => {
          const href = link.getAttribute('href') || '';
          return {
            id: href.slice(1),
            text: link.textContent.replace(/\s+/g, ' ').trim()
          };
        })
      : sections.slice(1).map((section) => {
          const heading = section.querySelector('h2, h3');
          return {
            id: section.id,
            text: heading ? heading.textContent.replace('§', '').replace(/\s+/g, ' ').trim() : section.id
          };
        });
    if (links.length) {
      const sub = document.createElement('ol');
      sub.className = 'sub';
      for (const item of links.slice(0, 12)) {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = path + '#' + item.id;
        a.textContent = item.text;
        li.appendChild(a);
        sub.appendChild(li);
      }
      activeLi.appendChild(sub);
    }
  }

  const input = document.querySelector('[data-doc-search]');
  const results = document.querySelector('[data-doc-search-results]');
  const index = window.SANDBOX_WORKSPACE_DOC_SEARCH || [];
  if (!input || !results || !index.length) return;

  function render(query) {
    const q = query.trim().toLowerCase();
    results.innerHTML = '';
    if (!q) return;
    const matches = index.filter((item) => {
      return (item.title + ' ' + item.text + ' ' + item.id + ' ' + (item.tags || '')).toLowerCase().includes(q);
    }).slice(0, 8);
    for (const item of matches) {
      const a = document.createElement('a');
      a.href = item.href;
      a.innerHTML = '<strong>' + item.title + '</strong><small>' + item.text + '</small>';
      results.appendChild(a);
    }
  }

  input.addEventListener('input', () => render(input.value));
})();
