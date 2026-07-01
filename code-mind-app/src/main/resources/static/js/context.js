// Resolves fetch/href URLs against the app's Spring `server.servlet.context-path`,
// so calls still work when this UI is reverse-proxied under a path prefix
// (e.g. /codemind-app/) instead of served at the origin root.
const CODEMIND_CONTEXT_PATH = (function () {
  const meta = document.querySelector('meta[name="context-path"]');
  const content = meta ? meta.content : '/';
  return content.replace(/\/$/, '');
})();

function apiUrl(path) {
  return CODEMIND_CONTEXT_PATH + path;
}
