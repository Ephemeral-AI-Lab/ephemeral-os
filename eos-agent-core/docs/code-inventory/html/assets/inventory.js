/* global document */
"use strict";

function normalize(value) {
  return value.toLowerCase();
}

function setupSearch(input) {
  var panel = input.closest(".panel") || document;
  var items = Array.prototype.slice.call(panel.querySelectorAll(".search-item"));
  input.addEventListener("input", function () {
    var query = normalize(input.value.trim());
    items.forEach(function (item) {
      var text = normalize(item.textContent || "");
      item.classList.toggle("is-hidden", query.length > 0 && !text.includes(query));
    });
  });
}

document.querySelectorAll(".search-input").forEach(setupSearch);
