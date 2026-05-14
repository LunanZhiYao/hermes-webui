/**
 * Shared browser storage cleanup when the authenticated user changes (ERP / SSO).
 * Loaded by the main app (before sessions.js) and should be loaded before login.js
 * on the login page so login and sessions.js stay aligned without duplicating lists.
 */
(function (global) {
  'use strict';

  var SESSION_VIEWED_COUNTS_KEY = 'hermes-session-viewed-counts';
  var SESSION_COMPLETION_UNREAD_KEY = 'hermes-session-completion-unread';
  var SESSION_OBSERVED_STREAMING_KEY = 'hermes-session-observed-streaming';

  function clearHermesBrowserStorageForUserSwitch() {
    try { localStorage.removeItem('hermes-webui-session'); } catch (_) {}
    try { localStorage.removeItem('hermes-jwt'); } catch (_) {}
    try { localStorage.removeItem(SESSION_VIEWED_COUNTS_KEY); } catch (_) {}
    try { localStorage.removeItem(SESSION_COMPLETION_UNREAD_KEY); } catch (_) {}
    try { localStorage.removeItem(SESSION_OBSERVED_STREAMING_KEY); } catch (_) {}
    try { localStorage.removeItem('hermes-date-groups-collapsed'); } catch (_) {}
    try { localStorage.removeItem('hermes-kanban-active-board'); } catch (_) {}
    try { localStorage.removeItem('hermes-webui-model'); } catch (_) {}
    try { localStorage.removeItem('hermes-webui-ws-files-seen-v1'); } catch (_) {}
    try {
      var drop = [];
      var i;
      for (i = 0; i < localStorage.length; i++) {
        var lk = localStorage.key(i);
        if (lk && lk.indexOf('hermes-webui-expanded:') === 0) drop.push(lk);
      }
      drop.forEach(function (k) { try { localStorage.removeItem(k); } catch (_) {} });
    } catch (_) {}
    try {
      var dss = [];
      for (i = 0; i < sessionStorage.length; i++) {
        var sk = sessionStorage.key(i);
        if (!sk) continue;
        if (sk.indexOf('hermes-queue-') === 0 || sk.indexOf('hermes-clarify-draft-') === 0) dss.push(sk);
      }
      dss.forEach(function (k) { try { sessionStorage.removeItem(k); } catch (_) {} });
    } catch (_) {}
  }

  global.clearHermesBrowserStorageForUserSwitch = clearHermesBrowserStorageForUserSwitch;
})(typeof window !== 'undefined' ? window : self);
