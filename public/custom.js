(function () {
  const MARKERS = {
    actor: { start: "\u2060\u2061\u2062\u2063", end: "\u2063\u2062\u2061\u2060" },
    user: { start: "\u2060\u2060\u2061\u2061", end: "\u2062\u2062\u2063\u2063" }
  };
  const MESSAGE_BOX = "data-ge-message-box";
  const SCENE_DONE = "data-ge-scene-enhanced";
  const THREAD_DONE = "data-ge-thread-enhanced";

  function normalizeText(text) {
    return (text || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  }

  function setImportant(el, prop, value) {
    if (!el || !el.style) return;
    el.style.setProperty(prop, value, "important");
  }

  function isInsideSidebar(el) {
    return Boolean(el && el.closest("aside, nav, [class*='Sidebar'], [class*='sidebar']"));
  }

  function isInsideComposer(el) {
    return Boolean(el && el.closest("form, .ge-composer-form, textarea, [contenteditable='true']"));
  }

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function getTextNodes(root) {
    const nodes = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (isInsideSidebar(parent) || isInsideComposer(parent)) return NodeFilter.FILTER_REJECT;
        if (parent.closest("script, style, button, [role='button'], footer")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let node;
    while ((node = walker.nextNode())) nodes.push(node);
    return nodes;
  }

  function commonAncestor(a, b) {
    if (!a || !b) return null;
    const aEls = [];
    let cur = a.nodeType === Node.TEXT_NODE ? a.parentElement : a;
    while (cur) {
      aEls.push(cur);
      cur = cur.parentElement;
    }
    cur = b.nodeType === Node.TEXT_NODE ? b.parentElement : b;
    while (cur) {
      if (aEls.includes(cur)) return cur;
      cur = cur.parentElement;
    }
    return null;
  }

  function findMarkerPairs(root, kind) {
    const { start, end } = MARKERS[kind];
    const nodes = getTextNodes(root);
    const pairs = [];
    let startNode = null;

    for (const node of nodes) {
      const value = node.nodeValue || "";
      if (value.includes(start)) startNode = node;
      if (value.includes(end)) {
        pairs.push([startNode || node, node, kind]);
        startNode = null;
      }
    }
    return pairs;
  }

  function removeMarkers(box) {
    const markers = Object.values(MARKERS).flatMap((m) => [m.start, m.end]);
    const nodes = getTextNodes(box);
    for (const node of nodes) {
      let value = node.nodeValue || "";
      for (const marker of markers) value = value.split(marker).join("");
      node.nodeValue = value;
    }

    box.querySelectorAll("p, div, span").forEach((el) => {
      if (!normalizeText(el.textContent || "") && el.children.length === 0) el.remove();
    });
  }

  function cleanMessageAncestors(box) {
    let cur = box.parentElement;
    for (let i = 0; i < 14 && cur && cur !== document.body; i += 1) {
      if (isInsideSidebar(cur) || isInsideComposer(cur)) break;
      if (cur.matches("main, [role='main']")) break;
      cur.classList.add("ge-message-ancestor-clean");
      setImportant(cur, "background", "transparent");
      setImportant(cur, "background-color", "transparent");
      setImportant(cur, "box-shadow", "none");
      setImportant(cur, "outline", "0");
      setImportant(cur, "border-color", "transparent");
      cur = cur.parentElement;
    }
  }

  function forceMessageBox(box, kind) {
    if (!box || isInsideSidebar(box) || isInsideComposer(box)) return;
    box.classList.add("ge-message-box");
    box.classList.toggle("ge-user-message-box", kind === "user");
    box.classList.toggle("ge-actor-message-box", kind === "actor");
    box.setAttribute(MESSAGE_BOX, "true");
    box.setAttribute("data-ge-message-kind", kind);

    setImportant(box, "box-sizing", "border-box");
    setImportant(box, "display", "block");
    setImportant(box, "width", "min(var(--ge-content-width), calc(100vw - 48px))");
    setImportant(box, "max-width", "var(--ge-content-width)");
    setImportant(box, "min-width", "min(280px, calc(100vw - 48px))");
    setImportant(box, "margin", kind === "user" ? "18px auto" : "22px auto");
    setImportant(box, "padding", kind === "user" ? "30px 38px" : "38px 42px");
    setImportant(box, "border", "1px solid var(--ge-line-strong)");
    setImportant(box, "background", "transparent");
    setImportant(box, "background-color", "transparent");
    setImportant(box, "box-shadow", "none");
    setImportant(box, "outline", "0");
    setImportant(box, "line-height", "2.05");
    setImportant(box, "color", "var(--ge-text)");
    if (kind === "user") setImportant(box, "font-style", "italic");
  }

  function splitSceneHeader(box) {
    if (!box || box.getAttribute(SCENE_DONE) === "true") return;
    if (box.getAttribute("data-ge-message-kind") !== "actor") return;
    if (box.querySelector(".ge-scene-header")) {
      box.setAttribute(SCENE_DONE, "true");
      return;
    }

    const text = box.innerText || box.textContent || "";
    const match = text.match(/^\s*((?:19|20|21)\d{2}년\s+\d{1,2}월\s+\d{1,2}일[^\n]*?\.\s*[^\n]+?)(?:\n|$)/);
    if (!match) return;

    const headerText = normalizeText(match[1].replace(/^\*\*|\*\*$/g, ""));
    const header = document.createElement("div");
    header.className = "ge-scene-header";
    header.textContent = headerText;

    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, null);
    let node;
    while ((node = walker.nextNode())) {
      const value = node.nodeValue || "";
      const idx = value.indexOf(match[1]);
      if (idx !== -1) {
        node.nodeValue = value.slice(0, idx) + value.slice(idx + match[1].length);
        break;
      }
      const normalizedValue = normalizeText(value);
      if (normalizedValue === headerText) {
        node.nodeValue = "";
        break;
      }
    }

    box.insertBefore(header, box.firstChild);
    box.querySelectorAll("p, div, span").forEach((el) => {
      if (el === header) return;
      if (!normalizeText(el.textContent || "") && el.children.length === 0) el.remove();
    });
    box.setAttribute(SCENE_DONE, "true");
  }

  function enhanceMarkedMessages() {
    const roots = Array.from(document.querySelectorAll("main, [role='main']")).filter(isVisible);
    for (const root of roots) {
      for (const kind of Object.keys(MARKERS)) {
        const pairs = findMarkerPairs(root, kind);
        for (const [startNode, endNode] of pairs) {
          let box = commonAncestor(startNode, endNode);
          if (!box) continue;
          if (box.matches("main, [role='main'], body, html")) {
            box = startNode.parentElement;
          }
          if (!box || box.matches("body, html")) continue;
          removeMarkers(box);
          cleanMessageAncestors(box);
          forceMessageBox(box, kind);
          splitSceneHeader(box);
          forceMessageBox(box, kind); // final pass: keep border after every cleanup
        }
      }
    }
  }

  function hideTinyAvatarsNearMessages() {
    document.querySelectorAll("main [class*='avatar'], main [class*='Avatar'], main [data-testid*='avatar']").forEach((el) => {
      if (isInsideComposer(el) || isInsideSidebar(el)) return;
      setImportant(el, "display", "none");
    });
  }

  function enhanceThreadItems() {
    const candidates = Array.from(document.querySelectorAll("aside a, aside button, [data-thread-id], .cl-thread-item"));
    for (const el of candidates) {
      if (el.getAttribute(THREAD_DONE) === "true") continue;
      const raw = normalizeText(el.textContent);
      if (!raw.includes(">")) continue;
      const parts = raw.split(" > ");
      if (parts.length < 2) continue;
      const title = parts[0].trim();
      const preview = parts.slice(1).join(" > ").trim();
      if (!title || !preview) continue;
      el.setAttribute(THREAD_DONE, "true");
      el.innerHTML = "";
      const titleNode = document.createElement("div");
      titleNode.className = "thread-card-title";
      titleNode.textContent = title;
      const previewNode = document.createElement("div");
      previewNode.className = "thread-card-preview";
      previewNode.textContent = `> ${preview}`;
      el.appendChild(titleNode);
      el.appendChild(previewNode);
    }
  }

  function findNearestContainerWithButton(field) {
    let cur = field;
    for (let i = 0; i < 9 && cur && cur !== document.body; i += 1) {
      if (cur.querySelector && cur.querySelector("button, [role='button']")) return cur;
      cur = cur.parentElement;
    }
    return null;
  }

  function findComposerForms() {
    const fields = Array.from(document.querySelectorAll("textarea, [contenteditable='true']"))
      .filter((field) => !isInsideSidebar(field));
    return fields
      .map((field) => field.closest("form") || findNearestContainerWithButton(field))
      .filter((form, idx, arr) => form && arr.indexOf(form) === idx);
  }

  function chooseSendButton(buttons) {
    if (!buttons.length) return null;
    const bySubmit = buttons.find((button) => (button.getAttribute("type") || "").toLowerCase() === "submit");
    if (bySubmit) return bySubmit;
    const byLabel = buttons.find((button) => {
      const label = `${button.getAttribute("aria-label") || ""} ${button.getAttribute("title") || ""}`.toLowerCase();
      return /send|submit|전송|보내기|arrow|위/.test(label);
    });
    return byLabel || buttons[buttons.length - 1];
  }

  function hideButton(button) {
    button.classList.add("ge-hidden-attachment");
    button.setAttribute("aria-hidden", "true");
    button.tabIndex = -1;
    setImportant(button, "display", "none");
    setImportant(button, "visibility", "hidden");
    setImportant(button, "pointer-events", "none");
  }

  function styleSendButton(button) {
    button.classList.add("ge-send-button");
    button.removeAttribute("aria-hidden");
    button.tabIndex = 0;
    setImportant(button, "display", "flex");
    setImportant(button, "visibility", "visible");
    setImportant(button, "pointer-events", "auto");
    setImportant(button, "background", "transparent");
    setImportant(button, "color", "var(--ge-accent)");
    setImportant(button, "border", "0");
    setImportant(button, "border-left", "1px solid var(--ge-line-strong)");
    setImportant(button, "box-shadow", "none");
    setImportant(button, "border-radius", "0");
    button.querySelectorAll("svg, path").forEach((node) => {
      setImportant(node, "color", "currentColor");
      setImportant(node, "stroke", "currentColor");
      setImportant(node, "fill", "none");
    });
  }

  function autoGrow(field) {
    if (!field) return;
    if (field.tagName && field.tagName.toLowerCase() === "textarea") {
      field.style.setProperty("overflow-y", "hidden", "important");
      field.style.setProperty("height", "auto", "important");
      field.style.setProperty("height", `${field.scrollHeight}px`, "important");
      return;
    }
    setImportant(field, "overflow-y", "hidden");
    setImportant(field, "min-height", "56px");
  }

  function markComposerAncestors(form) {
    let cur = form.parentElement;
    for (let i = 0; i < 8 && cur && cur !== document.body; i += 1) {
      if (isInsideSidebar(cur)) break;
      cur.classList.add("ge-composer-clean");
      setImportant(cur, "background", "transparent");
      setImportant(cur, "background-color", "transparent");
      setImportant(cur, "box-shadow", "none");
      cur = cur.parentElement;
    }
  }

  function enhanceComposer() {
    const forms = findComposerForms();
    for (const form of forms) {
      form.classList.add("ge-composer-form");
      markComposerAncestors(form);
      setImportant(form, "background", "transparent");
      setImportant(form, "background-color", "transparent");
      setImportant(form, "box-shadow", "none");
      setImportant(form, "border-radius", "0");
      setImportant(form, "overflow", "visible");
      const field = form.querySelector("textarea, [contenteditable='true']");
      if (field) {
        field.setAttribute("data-ge-autogrow", "true");
        setImportant(field, "background", "transparent");
        setImportant(field, "background-color", "transparent");
        setImportant(field, "box-shadow", "none");
        setImportant(field, "border", "0");
        setImportant(field, "outline", "0");
        setImportant(field, "overflow-y", "hidden");
        setImportant(field, "resize", "none");
        autoGrow(field);
      }
      const buttons = Array.from(form.querySelectorAll("button, [role='button']"));
      const sendButton = chooseSendButton(buttons);
      for (const button of buttons) {
        if (button === sendButton) styleSendButton(button);
        else hideButton(button);
      }
    }
  }

  function attachAutoGrowListener() {
    document.querySelectorAll("textarea, [contenteditable='true']").forEach((field) => {
      if (isInsideSidebar(field)) return;
      if (field.getAttribute("data-ge-autogrow-listener") === "true") return;
      field.setAttribute("data-ge-autogrow-listener", "true");
      field.addEventListener("input", () => autoGrow(field));
      field.addEventListener("keydown", () => setTimeout(() => autoGrow(field), 0));
      autoGrow(field);
    });
  }

  function removeChainlitDisclaimer() {
    const targetTexts = [
      "LLM은 실수할 수 있습니다",
      "중요한 정보는 확인하세요",
      "LLMs can make mistakes",
      "AI can make mistakes",
      "check important info",
      "Check important info"
    ];
    const nodes = Array.from(document.querySelectorAll("body *"));
    for (const el of nodes) {
      if (!el || isInsideSidebar(el) || isInsideComposer(el)) continue;
      const text = normalizeText(el.textContent || "");
      if (!text) continue;
      if (!targetTexts.some((target) => text.includes(target))) continue;
      const rect = el.getBoundingClientRect();
      if (text.length > 180 || rect.top < window.innerHeight * 0.50) continue;
      el.classList.add("ge-disclaimer-hidden");
      setImportant(el, "display", "none");
      setImportant(el, "visibility", "hidden");
      setImportant(el, "height", "0");
      setImportant(el, "min-height", "0");
      setImportant(el, "max-height", "0");
      setImportant(el, "margin", "0");
      setImportant(el, "padding", "0");
      setImportant(el, "border", "0");
      setImportant(el, "overflow", "hidden");
      setImportant(el, "pointer-events", "none");
    }
  }

  function showFloatingToast(text) {
    let toast = document.querySelector(".ge-floating-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "ge-floating-toast";
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    toast.classList.add("is-visible");
    clearTimeout(window.__geToastTimer);
    window.__geToastTimer = setTimeout(() => toast.classList.remove("is-visible"), 1600);
  }

  function runEnhancements() {
    enhanceThreadItems();
    enhanceComposer();
    attachAutoGrowListener();
    enhanceMarkedMessages();
    hideTinyAvatarsNearMessages();
    removeChainlitDisclaimer();
  }

  const observer = new MutationObserver(() => window.requestAnimationFrame(runEnhancements));
  function start() {
    runEnhancements();
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  window.GraphEngineToast = showFloatingToast;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  setTimeout(runEnhancements, 100);
  setTimeout(runEnhancements, 400);
  setTimeout(runEnhancements, 1000);
  setInterval(runEnhancements, 1200);
})();
