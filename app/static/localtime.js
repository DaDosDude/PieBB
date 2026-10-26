// Rewrites server-rendered times (<time class="bbtime" datetime="...Z">) into the visitor's
// own timezone, keeping phpBB2's "Sun Oct 25, 2026 11:05 am" format. Without JS the server time stays.
(function () {
  "use strict";
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function fmt(d) {
    var h = d.getHours(), ampm = h < 12 ? "am" : "pm";
    h = h % 12 || 12;
    var m = d.getMinutes();
    return DAYS[d.getDay()] + " " + MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear() +
      " " + h + ":" + (m < 10 ? "0" : "") + m + " " + ampm;
  }

  function run() {
    var nodes = document.querySelectorAll("time.bbtime[datetime]");
    for (var i = 0; i < nodes.length; i++) {
      var d = new Date(nodes[i].getAttribute("datetime"));
      if (!isNaN(d.getTime())) {
        nodes[i].textContent = fmt(d);
      }
    }
    var tz = document.getElementById("bbtz");
    try {
      var name = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (tz && name) tz.textContent = name;
    } catch (e) { /* old browser: keep the server timezone label */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
