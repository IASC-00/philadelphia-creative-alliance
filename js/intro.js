/* Homepage intro controller. Loaded (blocking, small) in <head> so the cover is up before
   first paint. Plays once per tab on the home page; never on a #link, never with reduced
   motion. Tries the 3D scene (js/intro3d.js, loaded only now); anything wrong with it
   (no WebGL, load error, lost context, a device too slow to keep up) runs the 2D version.
   Test hook: ?introtest=1 exposes window.__introSeek(seconds) and never auto-advances.
   Also ?intro2d=1 (force 2D) and ?introguard=0 (no slow-device check). */
(function () {
  'use strict';
  var KEY = 'pcaIntroSeen', END = 6.3, FAILSAFE = 9, LOAD_WAIT = 2.6;
  var q = location.search;
  var TEST = /[?&]introtest=1\b/.test(q), FORCE2D = /[?&]intro2d=1\b/.test(q), GUARD = !/[?&]introguard=0\b/.test(q);
  var path = location.pathname;
  if (!(path === '/' || /\/index\.html$/.test(path))) return;
  if (!TEST) {
    if (location.hash && location.hash !== '#') return;
    try { if (sessionStorage.getItem(KEY)) return; } catch (e) {}
    if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    try { sessionStorage.setItem(KEY, '1'); } catch (e) {}
  }
  var root = document.documentElement;
  root.classList.add('pca-intro', 'pca-intro-cover');
  var here = (document.currentScript && document.currentScript.src) || location.href;
  var url3d = new URL('intro3d.js', here).href;

  var ov, skip, mode = null, scene3d = null, done = false, raf = 0;
  var t = 0, last = 0, startedAt = 0, visible = 0, inerted = [];

  function rng(seed) {
    return function () {
      seed = seed + 0x6D2B79F5 | 0; var r = Math.imul(seed ^ seed >>> 15, 1 | seed);
      r = r + Math.imul(r ^ r >>> 7, 61 | r) ^ r; return ((r ^ r >>> 14) >>> 0) / 4294967296;
    };
  }

  function build() {
    ov = document.createElement('div');
    ov.className = 'pi-ov';
    ov.innerHTML = '<div class="pi-cells" aria-hidden="true"></div>' +
      '<div class="pi-2d" aria-hidden="true"><div class="pi-art2"><div class="pi-city"></div>' +
      '<svg class="pi-lines"></svg><p class="pi-words"></p></div></div>' +
      '<button class="pi-skip" type="button">Skip intro</button>';
    document.body.insertBefore(ov, document.body.firstChild);
    root.classList.remove('pca-intro-cover');
    skip = ov.querySelector('.pi-skip');
    skip.addEventListener('click', finish);
    document.addEventListener('keydown', onKey, true);
    // keep focus inside the intro: the page behind is inert until it ends
    Array.prototype.forEach.call(document.body.children, function (el) {
      if (el !== ov && !el.inert) { el.inert = true; inerted.push(el); }
    });
  }

  function onKey(e) { if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); finish(); } }

  /* ---------- 2D fallback: lights across a city grid connect, then set into the mark ---------- */
  var built2d = false;
  function run2d() {
    if (scene3d) { try { scene3d.dispose(); } catch (e) {} scene3d = null; }
    if (built2d || done) return; built2d = true;
    mode = '2d';
    ov.classList.remove('is-3d'); ov.classList.add('is-2d');
    var W = innerWidth, H = innerHeight, portrait = W / H < 0.9, R = rng(7);
    var HUE = ['#EC4A2C', '#2F63E6', '#F2B01E', '#DB2F86'];
    var art = ov.querySelector('.pi-art2'), svg = ov.querySelector('.pi-lines'), words = ov.querySelector('.pi-words');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    // the mark: 2x2 chips of 3x3 tesserae, like the header's four chips
    var s = Math.round(Math.min(portrait ? W * 0.075 : W * 0.032, H * 0.06, 34)), g = Math.max(2, Math.round(s * 0.18)), G = Math.round(s * 0.45);
    var chip = 3 * s + 2 * g, markW = 2 * chip + G;
    var fs = Math.round(s * 1.02), lineH = fs * 1.28, textW = fs * 0.62 * 1.1 * 12, textH = lineH * 3;
    words.style.setProperty('--pi-fs', fs + 'px');
    words.textContent = 'Philadelphia\nCreative\nAlliance';
    var mx, my, wx, wy;
    if (portrait) {
      var totH = markW + s * 1.4 + textH;
      mx = (W - markW) / 2; my = (H - totH) / 2; wx = (W - textW) / 2; wy = my + markW + s * 1.4;
      words.style.textAlign = 'center';
    } else {
      var totW = markW + s * 1.6 + textW;
      mx = (W - totW) / 2; my = (H - markW) / 2; wx = mx + markW + s * 1.6; wy = my + (markW - textH) / 2;
    }
    words.style.left = wx + 'px'; words.style.top = wy + 'px'; words.style.width = textW + 'px';
    // 36 lights spread across the screen (not too close), 9 of each mark colour
    var pts = [], tries = 0, minD = Math.sqrt(W * H / 36) * 0.62;
    while (pts.length < 36 && tries++ < 4000) {
      var x = W * (0.06 + R() * 0.88), y = H * (0.08 + R() * 0.84), ok = true;
      for (var k = 0; k < pts.length; k++) { if (Math.hypot(pts[k].x - x, pts[k].y - y) < minD) { ok = false; break; } }
      if (ok) pts.push({ x: x, y: y });
    }
    while (pts.length < 36) pts.push({ x: W * R(), y: H * R() });
    var cols = []; for (var c = 0; c < 36; c++) cols.push(c % 4);
    for (c = 35; c > 0; c--) { var j = Math.floor(R() * (c + 1)), tmp = cols[c]; cols[c] = cols[j]; cols[j] = tmp; }
    var slot = [0, 0, 0, 0], html = '';
    pts.forEach(function (p, i) {
      var h = cols[i], n = slot[h]++, cx = h % 2, cy = h > 1 ? 1 : 0;
      var tx = mx + cx * (chip + G) + (n % 3) * (s + g) + s / 2, ty = my + cy * (chip + G) + Math.floor(n / 3) * (s + g) + s / 2;
      html += '<i class="pi-dot" style="--c:' + HUE[h] + ';background:' + HUE[h] + ';left:' + p.x.toFixed(1) + 'px;top:' + p.y.toFixed(1) + 'px;' +
        '--d1:' + (0.3 + i * 0.04).toFixed(2) + 's;--d2:' + (3.35 + R() * 0.45).toFixed(2) + 's;' +
        '--tx:' + (tx - p.x).toFixed(1) + 'px;--ty:' + (ty - p.y).toFixed(1) + 'px;--ts:' + (s / 12).toFixed(3) + ';--tr:' + ((R() - 0.5) * 6).toFixed(1) + 'deg"></i>';
      p.h = h;
    });
    art.insertAdjacentHTML('beforeend', html);
    // network: the shortest set of threads that joins every light (grown from the first)
    var lines = '', inTree = [true], n = pts.length;
    for (var e = 1; e < n; e++) {
      var bestA = 0, bestB = 0, bd = 1e9;
      for (var a = 0; a < n; a++) if (inTree[a]) for (var b = 0; b < n; b++) if (!inTree[b]) {
        var d = Math.hypot(pts[a].x - pts[b].x, pts[a].y - pts[b].y); if (d < bd) { bd = d; bestA = a; bestB = b; }
      }
      inTree[bestB] = true;
      lines += '<line x1="' + pts[bestA].x.toFixed(1) + '" y1="' + pts[bestA].y.toFixed(1) + '" x2="' + pts[bestB].x.toFixed(1) + '" y2="' + pts[bestB].y.toFixed(1) +
        '" stroke="' + HUE[pts[bestB].h] + '" style="--len:' + Math.ceil(bd) + ';--d1:' + (1.3 + e * 0.04).toFixed(2) + 's"/>';
    }
    svg.innerHTML = lines;
    // the reveal: the dark is lifted tile by tile, starting at the mark
    var cell = Math.ceil(Math.max(W, H) / 9), cells = '', cxm = mx + markW / 2, cym = my + markW / 2, far = Math.hypot(W, H);
    for (var yy = 0; yy < H; yy += cell) for (var xx = 0; xx < W; xx += cell) {
      var dd = Math.hypot(xx + cell / 2 - cxm, yy + cell / 2 - cym) / far;
      cells += '<i class="pi-cell" style="left:' + xx + 'px;top:' + yy + 'px;width:' + (cell + 1) + 'px;height:' + (cell + 1) + 'px;' +
        '--d:' + (5.55 + dd * 0.45 + R() * 0.08).toFixed(2) + 's;--r:' + ((R() - 0.5) * 40).toFixed(0) + 'deg"></i>';
    }
    ov.querySelector('.pi-cells').innerHTML = cells;
    if (TEST) { seek2d(t); exposeHook(); return; }
    startClock(0);
  }

  function anims() { return ov.getAnimations ? ov.getAnimations({ subtree: true }) : []; }
  function seek2d(s) { anims().forEach(function (an) { an.pause(); an.currentTime = s * 1000; }); }

  /* ---------- clock (visible time only) ---------- */
  function startClock(from) {
    t = from; visible = from; last = performance.now(); startedAt = last;
    cancelAnimationFrame(raf); raf = requestAnimationFrame(tick);
  }
  var frames = [], guardDone = !GUARD;
  function tick(now) {
    if (done) return;
    var dt = Math.min((now - last) / 1000, 0.1); last = now;
    if (!document.hidden) { t += dt; visible += dt; }
    if (mode === '3d' && scene3d) {
      if (!guardDone) {
        frames.push(now);
        if (t > 0.5) {
          guardDone = true;
          var avg = (frames[frames.length - 1] - frames[0]) / Math.max(1, frames.length - 1);
          if (avg > 50) { run2d(); return; }
        }
      }
      scene3d.render(Math.min(t, END));
    }
    if (t >= END || visible >= FAILSAFE) { finish(); return; }
    raf = requestAnimationFrame(tick);
  }
  document.addEventListener('visibilitychange', function () { last = performance.now(); });

  function finish() {
    if (done) return; done = true;
    cancelAnimationFrame(raf);
    document.removeEventListener('keydown', onKey, true);
    if (scene3d) { try { scene3d.dispose(); } catch (e) {} scene3d = null; }
    if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
    inerted.forEach(function (el) { el.inert = false; });
    root.classList.remove('pca-intro', 'pca-intro-cover');
  }

  /* ---------- 3D attempt ---------- */
  function webglOK() {
    try {
      var c = document.createElement('canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
      if (!gl) return false;
      var lose = gl.getExtension('WEBGL_lose_context'); if (lose) lose.loseContext();
      return true;
    } catch (e) { return false; }
  }

  function try3d() {
    var settled = false;
    var timer = setTimeout(function () { if (!settled) { settled = true; run2d(); } }, TEST ? 120000 : LOAD_WAIT * 1000);
    import(url3d).then(function (m) {
      if (settled || done) return;
      return m.create(ov, {
        test: TEST,
        onLost: function () { if (mode === '3d' && !done) run2d(); }
      }).then(function (sc) {
        if (settled || done) { sc.dispose(); return; }
        settled = true; clearTimeout(timer);
        scene3d = sc; mode = '3d'; ov.classList.add('is-3d');
        if (TEST) { sc.render(0); exposeHook(); return; }
        startClock(0);
      });
    }).catch(function () { if (!settled) { settled = true; clearTimeout(timer); run2d(); } });
  }

  function start() {
    build();
    if (!FORCE2D && webglOK()) try3d(); else run2d();
  }

  // test only: deterministic frames; the hook stays alive after the last frame
  function exposeHook() {
    if (window.__introSeek) return;
    window.__introSeek = function (s) {
      t = s;
      var over = s >= END;
      ov.style.display = over ? 'none' : '';
      root.classList.toggle('pca-intro', !over);
      if (mode === '3d' && scene3d) scene3d.render(Math.min(s, END));
      else if (mode === '2d') seek2d(s);
      return mode;
    };
    window.__introMode = function () { return mode; };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
