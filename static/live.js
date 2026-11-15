(function () {
  "use strict";
  var page = document.body.dataset.page;
  var HOME = "#3987e5", AWAY = "#d95926";
  var STATE_TEXT = {won: "✔ Bejött", lost: "✘ Elbukott", on: "Jó úton", void: "Visszajár", open: "", off: ""};
  var STATE_CLASSES = ["st-won", "st-lost", "st-on", "st-void", "st-open", "st-off"];
  var LIVE = ["1H", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP", "HT"];
  var REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function ls(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; }
  }
  function ss(k, v) {
    try { if (v === undefined) return JSON.parse(sessionStorage.getItem(k) || "{}"); sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { return {}; }
  }
  function fetchJson(url) {
    return fetch(url, {cache: "no-store", credentials: "same-origin"}).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }
  function minute(f) {
    if (f.status === "HT") return "Félidő";
    if (f.status === "ET") return "Hosszabbítás " + (f.elapsed || "") + "'";
    if (f.status === "P") return "Tizenegyesek";
    if (f.status === "BT") return "Szünet";
    if (f.status === "FT") return "Vége";
    if (f.status === "AET") return "Hosszabbítás után";
    if (f.status === "PEN") return "Tizenegyesekkel";
    if (f.status === "PST") return "Elhalasztva";
    if (f.status === "CANC" || f.status === "ABD") return "Elmaradt";
    return f.elapsed ? f.elapsed + (f.extra ? "+" + f.extra : "") + "'" : "Élő";
  }
  function isLive(f) { return f.live === true || LIVE.indexOf(f.status) >= 0; }
  function setState(el, state) {
    STATE_CLASSES.forEach(function (c) { el.classList.remove(c); });
    if (state) el.classList.add("st-" + state);
  }
  function flash(el) {
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }
  function setPbar(root, p) {
    if (!root || !p) return;
    var v = [p.h * 100, p.d * 100, p.a * 100];
    ["ph", "pd", "pa"].forEach(function (c, i) { root.querySelector("." + c).style.width = v[i].toFixed(1) + "%"; });
    root.querySelectorAll(".pbar-legend b").forEach(function (b, i) { b.textContent = Math.round(v[i]) + "%"; });
  }

  /* ================================================================ értesítések, hang, konfetti */
  var toasts = document.getElementById("toasts");
  var bell = document.getElementById("bell");
  var actx = null;

  function notifyOn() { return ls("diaz_notify") === "1"; }
  function paintBell() {
    if (!bell) return;
    bell.textContent = notifyOn() ? "🔔" : "🔕";
    bell.setAttribute("aria-pressed", notifyOn() ? "true" : "false");
    bell.classList.toggle("on", notifyOn());
  }
  function unlockAudio() {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!actx) actx = new AC();
    if (actx.state === "suspended") actx.resume();
  }
  function playDing() {
    if (!actx) return;
    var t = actx.currentTime, o = actx.createOscillator(), g = actx.createGain();
    o.type = "sine"; o.frequency.value = 880;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.15, t + .02); g.gain.exponentialRampToValueAtTime(.001, t + .5);
    o.connect(g); g.connect(actx.destination); o.start(t); o.stop(t + .55);
  }
  function playGoal() {
    if (!actx || !notifyOn()) return;
    var t = actx.currentTime, len = actx.sampleRate * 2, buf = actx.createBuffer(1, len, actx.sampleRate), d = buf.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    var src = actx.createBufferSource(); src.buffer = buf;
    var bp = actx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 900; bp.Q.value = .6;
    var g = actx.createGain();
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.35, t + .15); g.gain.exponentialRampToValueAtTime(.001, t + 2);
    src.connect(bp); bp.connect(g); g.connect(actx.destination); src.start(t);
    [392, 523.25].forEach(function (f) {
      var o = actx.createOscillator(), og = actx.createGain();
      o.type = "sawtooth"; o.frequency.value = f;
      og.gain.setValueAtTime(0, t); og.gain.linearRampToValueAtTime(.1, t + .05); og.gain.exponentialRampToValueAtTime(.001, t + 1.1);
      o.connect(og); og.connect(actx.destination); o.start(t); o.stop(t + 1.2);
    });
  }
  function dismiss(el) {
    if (!el.parentNode) return;
    el.classList.add("out");
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 350);
  }
  function toast(text, ms) {
    if (!toasts) return;
    var el = document.createElement("div");
    el.className = "toast"; el.textContent = text;
    toasts.appendChild(el);
    setTimeout(function () { dismiss(el); }, ms || 4500);
  }
  function goalBanner(f, side) {
    if (!toasts) return;
    var el = document.createElement("div");
    el.className = "goal-banner";
    var team = side === "home" ? f.home : f.away;
    el.innerHTML = '<div class="big">GÓÓÓL!</div><div class="sub">⚽ ' + esc(team) + "</div>" +
      '<div class="sub">' + esc(f.home) + " <b>" + f.hg + " – " + f.ag + "</b> " + esc(f.away) +
      ' <span class="min">· ' + esc(minute(f)) + "</span></div>";
    el.addEventListener("click", function () { dismiss(el); });
    toasts.appendChild(el);
    setTimeout(function () { dismiss(el); }, 8000);
    playGoal();
    if (notifyOn() && "Notification" in window && Notification.permission === "granted" && document.hidden) {
      try {
        new Notification("⚽ GÓL! " + f.home + " " + f.hg + " – " + f.ag + " " + f.away,
          {body: team + " · " + minute(f), tag: "goal-" + f.home + "-" + f.away + "-" + (f.hg + f.ag)});
      } catch (e) { /* néhány böngésző csak service workerből engedi */ }
    }
  }
  function confetti() {
    if (REDUCED) return;
    var cv = document.getElementById("confetti");
    if (!cv) return;
    var dpr = window.devicePixelRatio || 1, W = window.innerWidth, H = window.innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    var ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    var colors = ["#3987e5", "#d95926", "#1baf7a", "#eda100", "#e87ba4", "#0ca30c", "#9085e9"];
    var parts = [];
    for (var i = 0; i < 170; i++) {
      var left = i % 2 === 0;
      parts.push({x: left ? 0 : W, y: H * .55, vx: (left ? 1 : -1) * (4 + Math.random() * 9), vy: -(8 + Math.random() * 12),
        w: 6 + Math.random() * 6, h: 4 + Math.random() * 5, r: Math.random() * 6, vr: (Math.random() - .5) * .4,
        c: colors[i % colors.length]});
    }
    var start = performance.now();
    (function frame(now) {
      var t = now - start;
      ctx.clearRect(0, 0, W, H);
      parts.forEach(function (p) {
        p.vy += .32; p.vx *= .992; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r);
        ctx.globalAlpha = Math.max(0, 1 - t / 4200);
        ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore();
      });
      if (t < 4200) requestAnimationFrame(frame); else ctx.clearRect(0, 0, W, H);
    })(start);
  }
  function celebrate(name) {
    toast("✔ Bejött: " + name, 6000);
    confetti();
  }

  // gólok és bejött tippek észlelése az /api/live és a meccsoldal adataiból (a lapok közt megmarad: sessionStorage)
  function handleGoals(id, f) {
    if (f.hg == null || f.ag == null) return;
    var prev = ss("diaz_scores"), cur = [f.hg, f.ag];
    var old = prev[id];
    prev[id] = cur;
    ss("diaz_scores", prev);
    if (!old) return;
    if (cur[0] + cur[1] > old[0] + old[1] && f.status !== "NS") goalBanner(f, cur[0] > old[0] ? "home" : "away");
  }
  function handleTips(id, states, names) {
    var prev = ss("diaz_tips");
    var done = [];
    try { done = JSON.parse(ls("diaz_celebrated") || "[]"); } catch (e) { done = []; }
    Object.keys(states).forEach(function (mk) {
      var key = id + "|" + mk, now = states[mk], was = prev[key];
      prev[key] = now;
      if (was !== undefined && was !== "won" && now === "won" && done.indexOf(key) < 0) {
        done.push(key);
        celebrate((names && names[mk]) || mk);
      }
    });
    ss("diaz_tips", prev);
    ls("diaz_celebrated", JSON.stringify(done.slice(-200)));
  }

  if (bell) {
    paintBell();
    bell.addEventListener("click", function () {
      if (notifyOn()) { ls("diaz_notify", "0"); paintBell(); toast("Gól-értesítések kikapcsolva"); return; }
      ls("diaz_notify", "1"); paintBell(); unlockAudio(); playDing();
      if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
      toast("Gól-értesítések bekapcsolva: hang és értesítés, amíg az oldal nyitva van valamelyik lapon.", 6000);
    });
    // a hang a böngésző szabálya miatt csak felhasználói kattintás után szólalhat meg
    if (notifyOn()) document.addEventListener("click", unlockAudio, {once: true});
  }

  /* ================================================================ lista */
  function updateIndex(d) {
    Object.keys(d.fixtures).forEach(function (id) {
      var f = d.fixtures[id];
      var card = document.querySelector('.match-card[data-fid="' + id + '"]');
      if (!card) return;
      var live = isLive(f);
      card.classList.toggle("is-live", live);
      var badge = card.querySelector(".live-badge"), kick = card.querySelector(".kick");
      if (live) {
        badge.hidden = false;
        badge.innerHTML = '<span class="live-dot"></span>' + esc(minute(f));
        kick.hidden = true;
      } else {
        badge.hidden = true;
        kick.hidden = false;
      }
      var sc = card.querySelector("[data-score]");
      if (f.hg != null && f.ag != null) {
        var txt = f.hg + " : " + f.ag;
        if (sc.textContent !== txt) {
          var had = /\d/.test(sc.textContent);
          sc.textContent = txt;
          if (had) flash(sc);
        }
      }
      card.querySelectorAll("[data-market]").forEach(function (chip) { setState(chip, f.tips[chip.dataset.market]); });
      if (f.probs) setPbar(card.querySelector(".pbar"), f.probs);
    });
  }

  /* ================================================================ meccsoldal */
  var lastScore = null;

  function renderEvents(events) {
    var box = document.getElementById("events"), h = document.getElementById("h-events");
    if (!box) return;
    if (!events.length) { box.hidden = true; h.hidden = true; return; }
    box.hidden = false; h.hidden = false;
    var icon = {goal: "⚽", owngoal: "⚽", pen_miss: "❌", sub: "🔁", var: "📺",
      yellow: '<span class="ecard y"></span>', red: '<span class="ecard r"></span>'};
    box.innerHTML = events.slice().reverse().map(function (e) {
      var min = (e.min != null ? e.min : "") + (e.extra ? "+" + e.extra : "") + "'";
      var label = e.kind === "owngoal" ? " (öngól)" : e.kind === "pen_miss" ? " (kihagyott 11-es)" : e.detail === "Penalty" ? " (11-es)" : "";
      var sub = "";
      if (e.kind === "goal" && e.assist) sub = "<small>gólpassz: " + esc(e.assist) + "</small>";
      if (e.kind === "sub" && e.sub_out) sub = "<small>ki: " + esc(e.sub_out) + "</small>";
      var name = '<span class="name">' + esc(e.player) + esc(label) + sub + "</span>";
      var ico = '<span class="ico">' + (icon[e.kind] || "") + "</span>";
      var side = e.side === "home" ? '<div class="who">' + name + ico + "</div>" : '<div class="who">' + ico + name + "</div>";
      var empty = '<div class="who"></div>';
      return '<div class="ev ' + e.side + " " + e.kind + '">' + (e.side === "home" ? side : empty) +
        '<div class="min">' + esc(min) + "</div>" + (e.side === "away" ? side : empty) + "</div>";
    }).join("");
  }
  function fmt(v, pct) { return pct ? Math.round(v) + "%" : (Math.round(v * 100) / 100).toString(); }
  function renderStats(stats) {
    var box = document.getElementById("stats"), h = document.getElementById("h-stats");
    if (!box) return;
    if (!stats.length) { box.hidden = true; h.hidden = true; return; }
    box.hidden = false; h.hidden = false;
    box.innerHTML = stats.map(function (s) {
      var total = s.home + s.away, hp = total ? s.home / total * 100 : 50, ap = total ? s.away / total * 100 : 50;
      return '<div class="st"><div class="st-head"><span class="h">' + fmt(s.home, s.pct) + '</span><span class="lbl">' + esc(s.label) +
        '</span><span class="a">' + fmt(s.away, s.pct) + '</span></div><div class="st-bars"><div class="h"><i style="width:' + hp.toFixed(1) +
        '%"></i></div><div class="a"><i style="width:' + ap.toFixed(1) + '%"></i></div></div></div>';
    }).join("");
  }

  // ---------------------------------------------------------------- felállás pályán
  function surname(n) {
    var p = String(n || "").trim().split(/\s+/);
    return p.length > 1 ? p.slice(1).join(" ") : p[0] || "";
  }
  function renderLineups(d) {
    var box = document.getElementById("lineups"), h = document.getElementById("h-lineups");
    if (!box) return;
    var L = d.lineups;
    if (!L || !L.home || !L.away) { box.hidden = true; h.hidden = true; return; }
    box.hidden = false; h.hidden = false;
    var marks = {}, subOutMin = {}, subbedIn = {};
    (d.events || []).forEach(function (e) {
      if (e.kind === "goal") (marks[e.player] = marks[e.player] || {g: 0, y: 0, r: 0}).g++;
      if (e.kind === "yellow") (marks[e.player] = marks[e.player] || {g: 0, y: 0, r: 0}).y++;
      if (e.kind === "red") (marks[e.player] = marks[e.player] || {g: 0, y: 0, r: 0}).r++;
      if (e.kind === "sub") { subOutMin[e.sub_out] = e.min; subbedIn[e.sub_in] = e.min; }
    });
    var s = '<svg class="pitch-svg" viewBox="0 0 105 68" role="img" aria-label="Kezdőcsapatok a pályán">';
    for (var i = 0; i < 10; i++) if (i % 2 === 0) s += '<rect class="stripe" x="' + (2 + i * 10.1) + '" y="2" width="10.1" height="64"/>';
    s += '<rect class="ln" x="2" y="2" width="101" height="64"/><line class="ln" x1="52.5" y1="2" x2="52.5" y2="66"/>' +
      '<circle class="ln" cx="52.5" cy="34" r="9.15"/><circle cx="52.5" cy="34" r=".5" fill="#ffffff55"/>' +
      '<rect class="ln" x="2" y="13.84" width="16.5" height="40.32"/><rect class="ln" x="86.5" y="13.84" width="16.5" height="40.32"/>' +
      '<rect class="ln" x="2" y="24.84" width="5.5" height="18.32"/><rect class="ln" x="97.5" y="24.84" width="5.5" height="18.32"/>';
    ["home", "away"].forEach(function (side) {
      var xi = L[side].xi, maxRow = 1, perRow = {};
      xi.forEach(function (p) { maxRow = Math.max(maxRow, p.row); perRow[p.row] = (perRow[p.row] || 0) + 1; });
      xi.forEach(function (p) {
        var x = maxRow > 1 ? 7 + (p.row - 1) / (maxRow - 1) * 39 : 7;
        var y = 6 + 56 * (p.col - .5) / perRow[p.row];
        if (side === "away") { x = 105 - x; y = 68 - y; }
        var mk = marks[p.name] || {g: 0, y: 0, r: 0}, off = subOutMin[p.name] != null;
        s += '<g class="pl ' + side + (off ? " off" : "") + '" transform="translate(' + x.toFixed(1) + " " + y.toFixed(1) + ')">' +
          '<circle class="dot" r="2.9"/><text class="num">' + esc(p.n) + '</text><text class="nm" y="5.6">' + esc(surname(p.name)) + "</text>";
        if (mk.g) s += '<text class="mk" x="3.2" y="-2.6">⚽' + (mk.g > 1 ? "×" + mk.g : "") + "</text>";
        if (mk.y && !mk.r) s += '<rect x="-4.4" y="-3.8" width="1.5" height="2.1" rx=".3" fill="#e3b341"/>';
        if (mk.r) s += '<rect x="-4.4" y="-3.8" width="1.5" height="2.1" rx=".3" fill="#f85149"/>';
        if (off) s += '<text class="mk" x="3.4" y="2.8" fill="#ff7b72">↓' + subOutMin[p.name] + "'</text>";
        s += "</g>";
      });
    });
    s += "</svg>";
    function head(side, color) {
      return '<span><i class="sw" style="background:' + color + '"></i>' + esc(d[side] || "") +
        ' <span class="formation">' + esc(L[side].formation || "") + "</span></span>";
    }
    function bench(side) {
      return '<div><h4>' + esc(d[side] || "") + " · cserék" + (L[side].coach ? " · edző: " + esc(L[side].coach) : "") + "</h4>" +
        L[side].subs.map(function (p) {
          var inn = subbedIn[p.name] != null;
          return '<div class="p"><span class="n">' + esc(p.n) + '</span><span class="' + (inn ? "in" : "") + '">' + esc(p.name) +
            (inn ? " " + subbedIn[p.name] + "'" : "") + "</span></div>";
        }).join("") + "</div>";
    }
    box.className = "card pitch-card";
    box.innerHTML = '<div class="pitch-head">' + head("home", HOME) + head("away", AWAY) + "</div>" + s +
      '<div class="bench">' + bench("home") + bench("away") + "</div>";
  }

  // ---------------------------------------------------------------- élő nyomás-grafikon
  function renderPressure(d) {
    var box = document.getElementById("pressure"), h = document.getElementById("h-pressure");
    if (!box) return;
    var s = d.series || [];
    if (!(d.live || d.finished) || !s.length) { box.hidden = true; h.hidden = true; return; }
    box.hidden = false; h.hidden = false;
    box.className = "card pad chart-card";
    if (s.length < 3) {
      box.innerHTML = '<div class="chart-empty">Az élő grafikon a meccs közben rajzolódik ki, amíg az oldal nyitva van valamelyik lapon.</div>';
      return;
    }
    // támadási mutató: lövés + kapura lövés (kétszeres súlyt kap) + fél szöglet
    var idx = function (sh, sot, c) { return sh + sot + .5 * c; };
    var last = s[s.length - 1].m || 90;
    var mk = (d.events || []).filter(function (e) { return e.kind === "goal" || e.kind === "owngoal"; }).map(function (e) {
      var home = (e.side === "home") !== (e.kind === "owngoal");
      return {x: e.min || 0, color: home ? HOME : AWAY, title: (e.min || "") + "' gól: " + (e.player || "")};
    });
    var host = box.querySelector(".chart");
    if (!host) { box.innerHTML = '<div class="chart" id="pressure-chart"></div>'; host = box.querySelector(".chart"); }
    var pts = function (side) {
      return s.map(function (r) {
        var sh = side === "h" ? r.hs : r.as, sot = side === "h" ? r.hsot : r.asot, c = side === "h" ? r.hc : r.ac;
        return {x: r.m, y: idx(sh, sot, c), tip: "lövés " + sh + " · kapura " + sot + " · szöglet " + c};
      });
    };
    DiazCharts.line(host, {
      aria: "Élő nyomás: összesített támadási mutató percenként", height: 230, xmin: 0, xmax: Math.max(90, last),
      xticks: [0, 15, 30, 45, 60, 75, 90].filter(function (v) { return v <= Math.max(90, last); }),
      xfmt: function (v) { return v + "'"; }, yfmt: function (v) { return (Math.round(v * 10) / 10).toString().replace(".", ","); },
      series: [{name: d.home, color: HOME, points: pts("h"), area: true}, {name: d.away, color: AWAY, points: pts("a"), area: true}],
      marks: mk,
      table: {head: ["Perc", d.home + " (mutató)", d.away + " (mutató)"], rows: s.map(function (r) {
        return [r.m + "'", idx(r.hs, r.hsot, r.hc), idx(r.as, r.asot, r.ac)]; })}
    });
    var note = box.querySelector(".note");
    if (!note) {
      note = document.createElement("p"); note.className = "note";
      note.textContent = "Támadási mutató: lövések + kapura lövések (kétszeres súly) + fél szöglet, összesítve a meccs kezdete óta. A pontok a gólokat jelölik. A felvételek az oldal nyitva tartása közben gyűlnek.";
      box.appendChild(note);
    }
  }

  function updateHeat(d, started) {
    var tbl = document.getElementById("heat");
    if (!tbl) return;
    tbl.querySelectorAll("td").forEach(function (td) {
      var h = +td.dataset.h, a = +td.dataset.a;
      var dead = started && d.live && (h < d.hg || a < d.ag);
      td.classList.toggle("dead", !!dead);
      td.classList.toggle("cur", !!(started && d.hg === h && d.ag === a && d.hg != null));
    });
  }

  function updateMatch(d) {
    var sb = document.getElementById("scoreboard");
    var live = isLive(d), started = d.status !== "NS" && d.status !== "TBD";
    sb.classList.toggle("is-live", live);
    var status = document.getElementById("sb-status");
    var hgEl = document.getElementById("sb-hg"), agEl = document.getElementById("sb-ag");
    if (started && d.hg != null && d.ag != null) {
      var key = d.hg + ":" + d.ag;
      document.querySelector(".sb-sep").textContent = ":";
      var kick = sb.querySelector(".sb-kick");
      if (kick) kick.remove();
      hgEl.textContent = d.hg; agEl.textContent = d.ag;
      if (lastScore !== null && lastScore !== key) { flash(hgEl); flash(agEl); }
      lastScore = key;
      status.className = "sb-status" + (live ? " live" : "");
      status.innerHTML = (live ? '<span class="live-dot"></span>' : "") + esc(minute(d));
    }
    var prog = document.getElementById("sb-progress");
    if (started) {
      prog.hidden = false;
      var pct = d.finished ? 100 : d.status === "HT" ? 50 : Math.min(100, ((d.elapsed || 0) / 90) * 100);
      document.getElementById("sb-bar").style.width = pct + "%";
    }
    var pc = document.getElementById("prob-card");
    if (pc && d.probs) setPbar(pc.querySelector(".pbar"), d.probs);
    var pl = document.getElementById("prob-live");
    if (pl) pl.hidden = !(live && d.probs);
    updateHeat(d, started);
    renderEvents(d.events || []);
    renderStats(d.stats || []);
    renderLineups(d);
    renderPressure(d);
    document.querySelectorAll("td[data-market]").forEach(function (td) {
      var st = d.tips[td.dataset.market], pill = td.querySelector(".state-pill");
      if (pill && st && STATE_TEXT[st]) { pill.className = "state-pill st-" + st; pill.textContent = STATE_TEXT[st]; }
    });
    handleTips(sb.dataset.fid, d.tips || {}, d.tip_names || {});
  }
  function pollMatch() {
    var sb = document.getElementById("scoreboard");
    if (!sb) return;
    fetchJson("/api/match/" + sb.dataset.fid + "/live").then(function (d) {
      if (d.enabled === false && d.hg == null) return;
      updateMatch(d);
    }).catch(function () {});
  }

  /* ================================================================ közös lekérdezés minden oldalon */
  function pollAll() {
    fetchJson("/api/live").then(function (d) {
      if (!d.enabled) return;
      Object.keys(d.fixtures).forEach(function (id) {
        var f = d.fixtures[id];
        handleGoals(id, f);
        handleTips(id, f.tips || {}, f.tip_names || {});
      });
      if (page === "index") updateIndex(d);
    }).catch(function () {});
  }
  window.DiazLive = {pollAll: pollAll, pollMatch: pollMatch};
  if (document.querySelector("header")) {  // belépett oldalakon (a belépő oldalon nincs fejléc)
    pollAll();
    setInterval(pollAll, 30000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) pollAll(); });
    if (page === "match" && document.getElementById("scoreboard")) {
      pollMatch();
      setInterval(pollMatch, 30000);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) pollMatch(); });
    }
  }
})();
