// Small posting conveniences. Everything here is optional: without JS the forms work the same.
(function () {
  "use strict";
  var dirty = false;

  function postingForm(el) {
    var f = el && el.closest ? el.closest("form") : null;
    return f && f.querySelector("textarea") ? f : null;
  }

  // Ctrl+Enter / Cmd+Enter in a textarea sends the form
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey) || e.target.tagName !== "TEXTAREA") return;
    var f = postingForm(e.target);
    if (!f) return;
    e.preventDefault();
    if (f.requestSubmit) f.requestSubmit(); else f.submit();
  });

  // Remember that something was typed, to warn before it is lost
  document.addEventListener("input", function (e) {
    if (e.target.tagName === "TEXTAREA" && e.target.value.trim()) dirty = true;
  });

  // One click, one post: disable the buttons once a posting form is sent.
  // (Deferred, so the clicked button's own name/value is still part of the submission.)
  document.addEventListener("submit", function (e) {
    var f = postingForm(e.target);
    if (!f) return;
    dirty = false;
    setTimeout(function () {
      f.querySelectorAll("button, input[type=submit]").forEach(function (b) { b.disabled = true; });
    }, 0);
    // If the browser comes back to this page (Back button), make it usable again
    window.addEventListener("pageshow", function () {
      f.querySelectorAll("button, input[type=submit]").forEach(function (b) { b.disabled = false; });
    }, { once: true });
  });

  // Formatting toolbar: wraps the selection (or a placeholder) in markdown
  var WRAP = {
    bold: ["**", "**", "bold text"], italic: ["*", "*", "italic text"], strike: ["~~", "~~", "struck text"],
    link: ["[", "](https://)", "link text"], image: ["![", "](https://)", "description"],
    spoiler: ["\n::: spoiler Spoiler\n", "\n:::\n", "hidden text"]
  };
  var LINES = { quote: "> ", list: "- ", olist: "1. " };

  function apply(ta, kind) {
    var start = ta.selectionStart, end = ta.selectionEnd, v = ta.value, sel = v.slice(start, end), out, selFrom, selTo;
    if (kind === "code") {
      kind = sel.indexOf("\n") >= 0 ? "codeblock" : "inline";
      WRAP.inline = ["`", "`", "code"];
      WRAP.codeblock = ["\n```\n", "\n```\n", "code"];
    }
    if (LINES[kind]) {
      var lineStart = v.lastIndexOf("\n", start - 1) + 1;
      var block = v.slice(lineStart, end) || "text";
      var n = 0;
      out = block.split("\n").map(function (l) {
        n += 1;
        return (kind === "olist" ? n + ". " : LINES[kind]) + l;
      }).join("\n");
      ta.setRangeText(out, lineStart, end, "end");
    } else {
      var w = WRAP[kind];
      if (!w) return;
      var text = sel || w[2];
      out = w[0] + text + w[1];
      ta.setRangeText(out, start, end, "end");
      // links/images: cursor right after "https://" to type the address;
      // otherwise select the placeholder so typing replaces it
      if (kind === "link" || kind === "image") {
        selFrom = selTo = start + w[0].length + text.length + 2 + "https://".length;
      } else if (!sel) {
        selFrom = start + w[0].length;
        selTo = selFrom + text.length;
      }
      if (selFrom !== undefined) ta.setSelectionRange(selFrom, selTo);
    }
    ta.focus();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  document.querySelectorAll(".mdbar").forEach(function (bar) { bar.hidden = false; });
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".mdbar button[data-md]") : null;
    if (!b) return;
    e.preventDefault();
    var ta = b.closest("td, form").querySelector("textarea");
    if (ta) apply(ta, b.getAttribute("data-md"));
  });

  window.addEventListener("beforeunload", function (e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });
})();
