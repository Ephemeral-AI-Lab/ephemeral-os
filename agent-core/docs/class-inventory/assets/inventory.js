const filterInput = document.querySelector("#filter");
const kindFilter = document.querySelector("#kind-filter");
const root = document.querySelector("#filter-root");

function applyFilter() {
  if (!root) return;
  const query = (filterInput?.value || "").trim().toLowerCase();
  const kind = kindFilter?.value || "";
  const richCards = root.querySelectorAll(".crate-card, .item-card, .type-card");
  richCards.forEach((card) => {
    const text = (card.dataset.filterText || card.textContent || "").toLowerCase();
    const kindList = card.dataset.kindList || card.dataset.kind || "";
    const kindMatch = !kind || kindList.split(/\s+/).includes(kind);
    card.classList.toggle("is-hidden", Boolean(query && !text.includes(query)) || !kindMatch);
  });
  root.querySelectorAll(".module-section").forEach((section) => {
    const visible = section.querySelector(".item-card:not(.is-hidden), .type-card:not(.is-hidden)");
    const sectionText = (section.dataset.filterText || section.textContent || "").toLowerCase();
    section.classList.toggle("is-hidden", Boolean(query && !sectionText.includes(query) && !visible));
  });
}

filterInput?.addEventListener("input", applyFilter);
kindFilter?.addEventListener("change", applyFilter);
