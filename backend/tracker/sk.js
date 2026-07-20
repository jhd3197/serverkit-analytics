/* serverkit-analytics tracker (sk.js) — privacy-first, cookieless.
   Source of truth. Build the served artifact with:
     node scripts/build-analytics-tracker.mjs
   which strips comments + trims whitespace into sk.min.js (served by the
   backend at GET /api/v1/analytics/tracker.js).

   No cookies. No localStorage. No fingerprinting. No third-party calls.
   Sends one small JSON beacon per pageview via navigator.sendBeacon (text/plain
   so no CORS preflight), honoring Do Not Track. Fails silently on any error so a
   disabled/removed panel never breaks the tracked site. */
(function () {
  var d = document;
  var s = d.currentScript;
  if (!s) { return; }

  var siteKey = s.getAttribute('data-site-key');
  if (!siteKey) { return; }

  /* Derive the collect endpoint from this script's own URL. */
  var src = s.src || '';
  var endpoint = src.replace(/tracker\.js(\?.*)?$/, 'collect');
  if (endpoint === src) { return; }

  /* Opt-in behaviors via data-* attributes. */
  var trackOutlinks = s.getAttribute('data-outlinks') === 'true';
  var honorDnt = s.getAttribute('data-respect-dnt') !== 'false';

  function dntOn() {
    var v = navigator.doNotTrack || window.doNotTrack || navigator.msDoNotTrack;
    return v === '1' || v === 'yes';
  }
  if (honorDnt && dntOn()) { return; }

  function screenClass() {
    var w = window.innerWidth || d.documentElement.clientWidth || 0;
    if (w < 576) { return 'xs'; }
    if (w < 768) { return 'sm'; }
    if (w < 992) { return 'md'; }
    if (w < 1200) { return 'lg'; }
    return 'xl';
  }

  function externalReferrer() {
    var r = d.referrer;
    if (!r) { return ''; }
    try {
      var u = new URL(r);
      if (u.host === location.host) { return ''; }
      return r;
    } catch (e) { return ''; }
  }

  function loadMs() {
    try {
      var nav = performance.getEntriesByType('navigation')[0];
      if (nav && nav.duration) { return Math.round(nav.duration); }
      var t = performance.timing;
      if (t && t.loadEventEnd && t.navigationStart) {
        return Math.max(0, t.loadEventEnd - t.navigationStart);
      }
    } catch (e) { }
    return null;
  }

  function post(body) {
    var payload = JSON.stringify(body);
    try {
      if (navigator.sendBeacon) {
        var blob = new Blob([payload], { type: 'text/plain' });
        if (navigator.sendBeacon(endpoint, blob)) { return; }
      }
    } catch (e) { }
    try {
      fetch(endpoint, {
        method: 'POST', body: payload, keepalive: true,
        headers: { 'Content-Type': 'text/plain' }, credentials: 'omit'
      });
    } catch (e) { }
  }

  var firstView = true;
  function send(type, extra) {
    var body = {
      k: siteKey,
      t: type,
      p: location.pathname,
      r: externalReferrer(),
      s: screenClass(),
      l: (navigator.language || '').slice(0, 16)
    };
    if (type === 'pageview' && firstView) {
      body.ms = loadMs();
      firstView = false;
    }
    if (extra) {
      for (var key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) {
          body[key] = extra[key];
        }
      }
    }
    post(body);
  }

  function pageview() { send('pageview'); }

  /* SPA navigations: patch history + listen for popstate; fire on path change. */
  var lastPath = location.pathname;
  function onNav() {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      pageview();
    }
  }
  function patch(name) {
    var orig = history[name];
    if (typeof orig !== 'function') { return; }
    history[name] = function () {
      var ret = orig.apply(this, arguments);
      setTimeout(onNav, 0);
      return ret;
    };
  }
  patch('pushState');
  patch('replaceState');
  window.addEventListener('popstate', onNav);

  /* Optional outbound + download click tracking. */
  var DOWNLOAD_RE = /\.(zip|rar|7z|gz|tar|pdf|docx?|xlsx?|pptx?|csv|dmg|exe|pkg|apk|mp3|mp4|mov|avi|iso)(\?|$)/i;
  if (trackOutlinks) {
    d.addEventListener('click', function (ev) {
      var a = ev.target && ev.target.closest ? ev.target.closest('a') : null;
      if (!a || !a.href) { return; }
      try {
        var u = new URL(a.href);
        if (u.host !== location.host) {
          send('outlink', { n: u.host });
        } else if (DOWNLOAD_RE.test(u.pathname)) {
          send('download', { n: u.pathname.slice(0, 128) });
        }
      } catch (e) { }
    }, true);
  }

  /* First pageview: wait for load so the timing metric is available. */
  if (d.readyState === 'complete') {
    pageview();
  } else {
    window.addEventListener('load', pageview);
  }
})();
