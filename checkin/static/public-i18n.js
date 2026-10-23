'use strict';
(() => {
  const catalog = JSON.parse(document.getElementById('public-strings').textContent);
  window.GuestUI = Object.freeze({
    language: catalog.language,
    t(source, values = {}) {
      const pattern = catalog.strings[source] || source;
      return pattern.replace(/\{([a-z_]+)\}/g, (match, key) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match);
    }
  });
  document.querySelectorAll('[data-ui-language]').forEach(link => {
    // Keep the ticket fragment in the browser. Never send credentials in a path
    // or query string, and never claim a card or check a guest in on a GET.
    const destination = new URL(location.href);
    destination.searchParams.set('lang', link.dataset.uiLanguage);
    link.href = destination.href;
  });
})();
