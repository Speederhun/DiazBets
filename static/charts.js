/* Kicsi, függőségmentes SVG diagramkönyvtár (vonal- és pontdiagram).
 * Szabályok: 2 px vonal, >= 8 px pont 2 px felületgyűrűvel, halk rács, legalább 2 adatsornál jelmagyarázat,
 * keresztvonal + buborék, és mindig van táblázatos nézet. A szövegek nem viselik az adatsor színét. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var SURFACE = "#1a212b";

  function svg(tag, attrs, parent) {
    var el = document.createElementNS(NS, tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function niceTicks(min, max, count) {
    if (min === max) { max = min + 1; }
    var span = max - min;
    var step = Math.pow(10, Math.floor(Math.log10(span / count)));
    var err = span / count / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var start = Math.floor(min / step + 1e-9) * step, end = Math.ceil(max / step - 1e-9) * step, out = [];
    for (var v = start; v <= end + step / 2; v += step) out.push(Math.round(v / step) * step);
    return out;
  }
  function fmtNum(v) {
    return (Math.round(v * 100) / 100).toString().replace(".", ",");
  }

  function legend(el, items) {
    var d = document.createElement("div");
    d.className = "chart-legend";
    d.innerHTML = items.map(function (it) {
      return '<span><i style="background:' + it.color + '"></i>' + esc(it.name) + "</span>";
    }).join("");
    el.appendChild(d);
  }
  function tableView(el, t) {
    if (!t) return;
    var det = document.createElement("details");
    det.className = "tbl";
    det.innerHTML = "<summary>Táblázatos nézet</summary><table><tr>" +
      t.head.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") + "</tr>" +
      t.rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + esc(c) + "</td>"; }).join("") + "</tr>"; }).join("") + "</table>";
    el.appendChild(det);
  }

  /* ---------------------------------------------------------------- vonaldiagram */
  function line(el, o) {
    el._o = o; el._type = "line";
    el.innerHTML = "";
    var series = o.series.filter(function (s) { return s.points.length; });
    if (!series.length) { el.innerHTML = '<div class="chart-empty">' + esc(o.empty || "Még nincs elég adat.") + "</div>"; return; }
    if (series.length >= 2) legend(el, series);

    var W = Math.max(280, el.clientWidth || 600), H = o.height || 240;
    var m = {l: 46, r: 16, t: 12, b: 26};
    var xs = [], ys = [];
    series.forEach(function (s) { s.points.forEach(function (p) { xs.push(p.x); ys.push(p.y); }); });
    var xmin = o.xmin != null ? o.xmin : Math.min.apply(null, xs);
    var xmax = o.xmax != null ? o.xmax : Math.max.apply(null, xs);
    if (xmax === xmin) xmax = xmin + 1;
    var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
    if (o.includeZero) { ymin = Math.min(ymin, 0); ymax = Math.max(ymax, 0); }
    if (o.ymin != null) ymin = o.ymin;
    if (o.ymax != null) ymax = o.ymax;
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    var yt = niceTicks(ymin, ymax, 4);
    ymin = yt[0]; ymax = yt[yt.length - 1];
    var X = function (v) { return m.l + (v - xmin) / (xmax - xmin) * (W - m.l - m.r); };
    var Y = function (v) { return H - m.b - (v - ymin) / (ymax - ymin) * (H - m.t - m.b); };
    var yfmt = o.yfmt || fmtNum, xfmt = o.xfmt || fmtNum;

    var root = svg("svg", {viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.aria || "Diagram"}, null);
    el.appendChild(root);
    // rács + y tengely
    yt.forEach(function (t) {
      svg("line", {x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), "class": "grid"}, root);
      var tx = svg("text", {x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", "class": "axis-t"}, root);
      tx.textContent = yfmt(t);
    });
    if (o.zero && ymin < 0 && ymax > 0) svg("line", {x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), "class": "zero"}, root);
    var xt = o.xticks || niceTicks(xmin, xmax, Math.max(2, Math.floor((W - m.l - m.r) / 90)));
    xt.forEach(function (t) {
      if (t < xmin || t > xmax) return;
      var tx = svg("text", {x: X(t), y: H - 6, "text-anchor": "middle", "class": "axis-t"}, root);
      tx.textContent = xfmt(t);
    });
    // területek, vonalak
    series.forEach(function (s) {
      var d = s.points.map(function (p, i) { return (i ? "L" : "M") + X(p.x).toFixed(1) + " " + Y(p.y).toFixed(1); }).join(" ");
      if (s.area) {
        var base = Y(Math.max(ymin, Math.min(ymax, o.zero ? 0 : ymin)));
        var a = d + " L" + X(s.points[s.points.length - 1].x).toFixed(1) + " " + base + " L" + X(s.points[0].x).toFixed(1) + " " + base + " Z";
        svg("path", {d: a, fill: s.color, "fill-opacity": 0.1}, root);
      }
      svg("path", {d: d, stroke: s.color, "class": "ln"}, root);
    });
    // jelölők (pl. gólok)
    (o.marks || []).forEach(function (mk) {
      if (mk.x < xmin || mk.x > xmax) return;
      svg("line", {x1: X(mk.x), x2: X(mk.x), y1: m.t + 8, y2: H - m.b, stroke: mk.color, "stroke-opacity": .35, "stroke-width": 1}, root);
      svg("circle", {cx: X(mk.x), cy: m.t + 8, r: 5, fill: mk.color, stroke: SURFACE, "stroke-width": 2}, root);
      var t = svg("title", {}, root.lastChild); t.textContent = mk.title || "";
    });
    // végpont: pont 2 px gyűrűvel + a végérték (csak egy adatsornál, ott nincs ütközés)
    series.forEach(function (s) {
      var p = s.points[s.points.length - 1];
      svg("circle", {cx: X(p.x), cy: Y(p.y), r: 4, fill: s.color, stroke: SURFACE, "stroke-width": 2}, root);
      if (series.length === 1) {
        var t = svg("text", {x: X(p.x), y: Y(p.y) - 10, "text-anchor": "end", "class": "axis-t", style: "fill:#e6edf3;font-weight:700"}, root);
        t.textContent = yfmt(p.y);
      }
    });
    // hover réteg
    var tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; el.appendChild(tip);
    var cross = svg("line", {y1: m.t, y2: H - m.b, "class": "cross", visibility: "hidden"}, root);
    var dots = series.map(function (s) { return svg("circle", {r: 5, fill: s.color, stroke: SURFACE, "stroke-width": 2, visibility: "hidden"}, root); });
    var hit = svg("rect", {x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent"}, root);
    function nearest(s, xv) {
      var best = 0, bd = Infinity;
      for (var i = 0; i < s.points.length; i++) { var dd = Math.abs(s.points[i].x - xv); if (dd < bd) { bd = dd; best = i; } }
      return s.points[best];
    }
    function move(ev) {
      var r = root.getBoundingClientRect();
      var cx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) * (W / r.width);
      var xv = xmin + (cx - m.l) / (W - m.l - m.r) * (xmax - xmin);
      var pts = series.map(function (s) { return nearest(s, xv); });
      var px = X(pts[0].x);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      dots.forEach(function (d, i) { d.setAttribute("cx", X(pts[i].x)); d.setAttribute("cy", Y(pts[i].y)); d.setAttribute("visibility", "visible"); });
      var head = pts[0].label ? esc(pts[0].label) : esc(xfmt(pts[0].x));
      var body = series.map(function (s, i) {
        return '<div><i class="sw" style="background:' + s.color + '"></i> ' + esc(s.name) + ': <b>' + esc(yfmt(pts[i].y)) + "</b>" +
          (pts[i].tip ? ' <span style="color:#8b98a8">' + esc(pts[i].tip) + "</span>" : "") + "</div>";
      }).join("");
      tip.innerHTML = "<div style=\"color:#8b98a8;margin-bottom:2px\">" + head + "</div>" + body;
      tip.hidden = false;
      var box = el.getBoundingClientRect();
      var left = r.left - box.left + px * (r.width / W);
      tip.style.left = Math.max(70, Math.min(box.width - 70, left)) + "px";
      tip.style.top = (r.top - box.top + Y(pts[0].y) * (r.height / H)) + "px";
    }
    function leave() { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dots.forEach(function (d) { d.setAttribute("visibility", "hidden"); }); }
    hit.addEventListener("mousemove", move); hit.addEventListener("mouseleave", leave);
    hit.addEventListener("touchstart", move, {passive: true}); hit.addEventListener("touchmove", move, {passive: true});
    hit.addEventListener("touchend", leave);
    tableView(el, o.table);
  }

  /* ---------------------------------------------------------------- kalibráció (pontdiagram) */
  function calib(el, bands) {
    el._o = bands; el._type = "calib";
    el.innerHTML = "";
    if (!bands.length) { el.innerHTML = '<div class="chart-empty">Még nincs elég elszámolt tipp.</div>'; return; }
    legend(el, [{name: "Tökéletes kalibráció (átló)", color: "#8b98a8"}, {name: "Tényleges találati arány", color: "#3987e5"}]);
    var W = Math.max(280, el.clientWidth || 600), H = Math.min(340, Math.round(W * 0.72));
    var m = {l: 46, r: 16, t: 12, b: 34};
    var lo = 30, hi = 100, ylo = 0;
    var X = function (v) { return m.l + (v - lo) / (hi - lo) * (W - m.l - m.r); };
    var Y = function (v) { return H - m.b - (v - ylo) / (hi - ylo) * (H - m.t - m.b); };
    var root = svg("svg", {viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Kalibrációs diagram: megadott esély és tényleges találati arány"}, null);
    el.appendChild(root);
    [0, 20, 40, 60, 80, 100].forEach(function (t) {
      svg("line", {x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), "class": "grid"}, root);
      var ty = svg("text", {x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", "class": "axis-t"}, root); ty.textContent = t + "%";
    });
    [30, 40, 50, 60, 70, 80, 90, 100].forEach(function (t) {
      var tx = svg("text", {x: X(t), y: H - 16, "text-anchor": "middle", "class": "axis-t"}, root); tx.textContent = t + "%";
    });
    var ax = svg("text", {x: (m.l + W - m.r) / 2, y: H - 2, "text-anchor": "middle", "class": "axis-t"}, root);
    ax.textContent = "Megadott esély";
    svg("line", {x1: X(lo), y1: Y(lo), x2: X(hi), y2: Y(hi), stroke: "#8b98a8", "stroke-width": 2, "stroke-linecap": "round"}, root);
    var tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; el.appendChild(tip);
    bands.forEach(function (b) {
      var c = svg("circle", {cx: X(b.predicted), cy: Y(b.actual), r: 6, fill: "#3987e5", stroke: SURFACE, "stroke-width": 2, tabindex: 0}, root);
      function show() {
        var r = root.getBoundingClientRect(), box = el.getBoundingClientRect();
        tip.innerHTML = "<b>" + esc(b.label) + "</b><div>Átlagos esély: <b>" + fmtNum(b.predicted) + "%</b></div><div>Tényleges: <b>" + fmtNum(b.actual) +
          "%</b></div><div style=\"color:#8b98a8\">" + b.n + " tipp</div>";
        tip.hidden = false;
        tip.style.left = (r.left - box.left + X(b.predicted) * (r.width / W)) + "px";
        tip.style.top = (r.top - box.top + Y(b.actual) * (r.height / H) - 4) + "px";
      }
      c.addEventListener("mouseenter", show); c.addEventListener("focus", show);
      c.addEventListener("mouseleave", function () { tip.hidden = true; }); c.addEventListener("blur", function () { tip.hidden = true; });
      c.addEventListener("touchstart", show, {passive: true});
    });
    tableView(el, {head: ["Sáv", "Tipp", "Átlagos esély %", "Tényleges %"], rows: bands.map(function (b) {
      return [b.label, b.n, fmtNum(b.predicted), fmtNum(b.actual)]; })});
  }

  window.DiazCharts = {line: line, calib: calib};

  // Átméretezéskor újrarajzolás
  var timer;
  window.addEventListener("resize", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      document.querySelectorAll(".chart").forEach(function (el) {
        if (!el._o) return;
        (el._type === "calib" ? calib : line)(el, el._o);
      });
    }, 150);
  });

  /* ---------------------------------------------------------------- előzmények oldal */
  function initHistory() {
    var node = document.getElementById("chart-data");
    if (!node) return;
    var d = JSON.parse(node.textContent);
    var BLUE = "#3987e5", ORANGE = "#d95926";
    line(document.getElementById("chart-profit"), {
      aria: "Tőkegörbe: összesített nyereség egységben", height: 240, zero: true, includeZero: true,
      empty: "Még nincs elszámolt tipp szorzóval.",
      xfmt: function (v) { return Math.round(v) + "."; },
      yfmt: function (v) { return (v > 0 ? "+" : "") + fmtNum(v); },
      series: [{name: "Összesített nyereség (egység)", color: BLUE, area: true,
        points: d.profit.map(function (p) { return {x: p.x, y: p.y, label: p.x + ". tipp · " + p.label}; })}],
      table: {head: ["#", "Tipp", "Összesített nyereség"], rows: d.profit.map(function (p) { return [p.x, p.label, p.y]; })}
    });
    line(document.getElementById("chart-hit"), {
      aria: "Tényleges és várt találati arány", height: 240,
      xfmt: function (v) { return Math.round(v) + "."; },
      yfmt: function (v) { return fmtNum(v) + "%"; },
      series: [
        {name: "Tényleges találati arány", color: BLUE, points: d.hit.map(function (p) { return {x: p.x, y: p.actual, label: p.x + ". tipp · " + p.label}; })},
        {name: "Várt (a modell esélyei szerint)", color: ORANGE, points: d.hit.map(function (p) { return {x: p.x, y: p.expected, label: p.x + ". tipp · " + p.label}; })}
      ],
      table: {head: ["#", "Tényleges %", "Várt %"], rows: d.hit.map(function (p) { return [p.x, fmtNum(p.actual), fmtNum(p.expected)]; })}
    });
    calib(document.getElementById("chart-calib"), d.calib);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initHistory);
  else initHistory();
})();
