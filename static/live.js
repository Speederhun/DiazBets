(function () {
  "use strict";
  var page = document.body.dataset.page;
  var STATE_TEXT = {won: "✔ Bejött", lost: "✘ Elbukott", on: "Jó úton", void: "Visszajár", open: "", off: ""};
  var STATE_CLASSES = ["st-won", "st-lost", "st-on", "st-void", "st-open", "st-off"];
  var LIVE = ["1H", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
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
    var m = f.elapsed ? f.elapsed + (f.extra ? "+" + f.extra : "") + "'" : "Élő";
    return m;
  }
  function isLive(f) { return f.live === true || LIVE.indexOf(f.status) >= 0 || f.status === "HT"; }
  function setState(el, state) {
    STATE_CLASSES.forEach(function (c) { el.classList.remove(c); });
    if (state) el.classList.add("st-" + state);
  }
  function flash(el) {
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }
  function fetchJson(url) {
    return fetch(url, {cache: "no-store", credentials: "same-origin"}).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }

  // ------------------------------------------------------------ lista
  function updateIndex(d) {
    if (!d.enabled) return;
    Object.keys(d.fixtures).forEach(function (id) {
      var f = d.fixtures[id];
      var card = document.querySelector('.match-card[data-fid="' + id + '"]');
      if (!card) return;
      var live = isLive(f);
      card.classList.toggle("is-live", live);
      var badge = card.querySelector(".live-badge");
      var kick = card.querySelector(".kick");
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
      card.querySelectorAll("[data-market]").forEach(function (chip) {
        setState(chip, f.tips[chip.dataset.market]);
      });
    });
  }
  function pollIndex() {
    if (document.hidden) return;
    fetchJson("/api/live").then(updateIndex).catch(function () {});
  }

  // ------------------------------------------------------------ meccsoldal
  var lastScore = null;
  function renderEvents(events) {
    var box = document.getElementById("events");
    var h = document.getElementById("h-events");
    if (!events.length) { box.hidden = true; h.hidden = true; return; }
    box.hidden = false; h.hidden = false;
    var icon = {goal: "⚽", owngoal: "⚽", pen_miss: "❌", sub: "🔁", var: "📺", yellow: '<span class="ecard y"></span>', red: '<span class="ecard r"></span>'};
    var rows = events.slice().reverse().map(function (e) {
      var min = (e.min != null ? e.min : "") + (e.extra ? "+" + e.extra : "") + "'";
      var label = e.kind === "owngoal" ? " (öngól)" : e.kind === "pen_miss" ? " (kihagyott 11-es)" : e.detail === "Penalty" ? " (11-es)" : "";
      var sub = "";
      if (e.kind === "goal" && e.assist) sub = "<small>gólpassz: " + esc(e.assist) + "</small>";
      if (e.kind === "sub" && e.assist) sub = "<small>be: " + esc(e.assist) + "</small>";
      var side = '<div class="who"><span class="ico">' + (icon[e.kind] || "") + '</span><span class="name">' + esc(e.player) + esc(label) + sub + "</span></div>";
      var empty = '<div class="who"></div>';
      var left = e.side === "home" ? side : empty;
      var right = e.side === "away" ? side : empty;
      if (e.side === "home") left = '<div class="who"><span class="name">' + esc(e.player) + esc(label) + sub + '</span><span class="ico">' + (icon[e.kind] || "") + "</span></div>";
      return '<div class="ev ' + e.side + " " + e.kind + '">' + left + '<div class="min">' + esc(min) + "</div>" + right + "</div>";
    });
    box.innerHTML = rows.join("");
  }
  function fmt(v, pct) {
    if (pct) return Math.round(v) + "%";
    return (Math.round(v * 100) / 100).toString();
  }
  function renderStats(stats) {
    var box = document.getElementById("stats");
    var h = document.getElementById("h-stats");
    if (!stats.length) { box.hidden = true; h.hidden = true; return; }
    box.hidden = false; h.hidden = false;
    box.innerHTML = stats.map(function (s) {
      var total = s.home + s.away;
      var hp = total ? (s.home / total) * 100 : 50;
      var ap = total ? (s.away / total) * 100 : 50;
      return '<div class="st"><div class="st-head"><span class="h">' + fmt(s.home, s.pct) + '</span><span class="lbl">' + esc(s.label) +
        '</span><span class="a">' + fmt(s.away, s.pct) + '</span></div><div class="st-bars"><div class="h"><i style="width:' + hp.toFixed(1) +
        '%"></i></div><div class="a"><i style="width:' + ap.toFixed(1) + '%"></i></div></div></div>';
    }).join("");
  }
  function updateMatch(d) {
    var sb = document.getElementById("scoreboard");
    var live = isLive(d);
    sb.classList.toggle("is-live", live);
    var status = document.getElementById("sb-status");
    var hgEl = document.getElementById("sb-hg"), agEl = document.getElementById("sb-ag");
    var started = d.status !== "NS" && d.status !== "TBD";
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
    renderEvents(d.events || []);
    renderStats(d.stats || []);
    document.querySelectorAll("td[data-market]").forEach(function (td) {
      var st = d.tips[td.dataset.market];
      var pill = td.querySelector(".state-pill");
      if (!pill) return;
      if (st && STATE_TEXT[st]) {
        pill.className = "state-pill st-" + st;
        pill.textContent = STATE_TEXT[st];
      }
    });
    if (d.corners != null && d.stats && live) {
      /* a szögletszámot a statisztika sávok mutatják */
    }
  }
  function pollMatch() {
    if (document.hidden) return;
    var id = document.getElementById("scoreboard").dataset.fid;
    fetchJson("/api/match/" + id + "/live").then(function (d) {
      if (d.enabled === false && !d.hg && d.hg !== 0) return;
      updateMatch(d);
    }).catch(function () {});
  }

  // ------------------------------------------------------------ indítás
  if (page === "index") {
    pollIndex();
    setInterval(pollIndex, 30000);
    document.addEventListener("visibilitychange", pollIndex);
  } else if (page === "match" && document.getElementById("scoreboard")) {
    pollMatch();
    setInterval(pollMatch, 30000);
    document.addEventListener("visibilitychange", pollMatch);
  }
})();
