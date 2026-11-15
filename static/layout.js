(function () {
  "use strict";
  var leagues = Array.prototype.slice.call(document.querySelectorAll("details.league"));
  var FKEY = "diaz_filter", CKEY = "diaz_collapsed";
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* nem kritikus */ } }
  function collapsed() { try { return JSON.parse(get(CKEY) || "{}"); } catch (e) { return {}; } }

  var filter = get(FKEY) || "all";
  if (["all", "live", "value"].indexOf(filter) < 0) filter = "all";

  // ligák lenyitása/becsukása: a választás ligánként megmarad a lapok között
  var st = collapsed();
  leagues.forEach(function (d) {
    if (st[d.dataset.lid] === 1) d.open = false;
    d.addEventListener("toggle", function () {
      var s = collapsed();
      if (d.open) delete s[d.dataset.lid]; else s[d.dataset.lid] = 1;
      set(CKEY, JSON.stringify(s));
    });
  });

  function apply() {
    var any = false;
    leagues.forEach(function (d) {
      var cards = d.querySelectorAll(".match-card"), shown = 0, live = 0;
      cards.forEach(function (c) {
        var isLive = c.classList.contains("is-live");
        if (isLive) live++;
        var show = filter === "all" || (filter === "live" && isLive) || (filter === "value" && c.classList.contains("has-value"));
        c.hidden = !show;
        if (show) shown++;
      });
      d.hidden = shown === 0;
      if (shown) any = true;
      var lv = d.querySelector(".lg-live");
      if (lv) { lv.hidden = !live; lv.textContent = live ? "● " + live + " élő" : ""; }
      var cnt = d.querySelector(".lg-count");
      if (cnt) cnt.textContent = (filter === "all" ? cards.length : shown + " / " + cards.length) + " meccs";
    });
    var empty = document.querySelector(".empty-filter");
    if (empty) empty.hidden = !(leagues.length && !any);
    document.querySelectorAll("#toolbar [data-filter]").forEach(function (b) {
      var on = b.dataset.filter === filter;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  document.querySelectorAll("#toolbar [data-filter]").forEach(function (b) {
    b.addEventListener("click", function () { filter = b.dataset.filter; set(FKEY, filter); apply(); });
  });
  var ca = document.getElementById("collapse-all"), ea = document.getElementById("expand-all");
  if (ca) ca.addEventListener("click", function () { leagues.forEach(function (d) { d.open = false; }); });
  if (ea) ea.addEventListener("click", function () { leagues.forEach(function (d) { d.open = true; }); });

  window.DiazLayout = {refresh: apply};
  apply();
})();
