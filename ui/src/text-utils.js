/*
 * Small, security-sensitive string utilities shared across the UI:
 *   escapeHtml - escape a value for safe interpolation into HTML (incl. attributes)
 *   fileUrl    - build a working file:// URL from a Windows/POSIX path
 *
 * Both had real bugs before (an unescaped " opened an attribute-injection point;
 * a `?t=` cache-bust ended up in the file path and 404'd), so they're worth
 * pinning with tests. Extracted here as a dual-mode module: it defines the same
 * BARE globals the classic scripts already call (escapeHtml(...), fileUrl(...))
 * and also exports them for `node --test`. Must load before renderer.js.
 */
(function (root) {
  'use strict';

  // Escape for HTML text AND attribute contexts. A plain textContent round-trip
  // leaves " and ' intact, which made interpolating into attr="..." unsafe.
  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Build a file:// URL from a Windows or POSIX path.
  //  - Backslashes are normalised; spaces/# etc. are percent-encoded (‘:’ kept
  //    so drive letters stay readable).
  //  - file: URLs have no query, so cache-busting uses a #fragment (ignored by
  //    the filesystem but enough to make the browser refetch).
  function fileUrl(filePath, cacheBust) {
    if (!filePath) return '';
    const normalized = String(filePath).replace(/\\/g, '/');
    const encoded = normalized
      .split('/')
      .map((segment) => encodeURIComponent(segment).replace(/%3A/gi, ':'))
      .join('/');
    const prefix = encoded.startsWith('/') ? 'file://' : 'file:///';
    return `${prefix}${encoded}${cacheBust ? `#t=${Date.now()}` : ''}`;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { escapeHtml: escapeHtml, fileUrl: fileUrl };
  }
  // Bare globals so the existing classic-script call sites keep working unchanged.
  root.escapeHtml = escapeHtml;
  root.fileUrl = fileUrl;
})(typeof window !== 'undefined' ? window : this);
