const messageScroll = document.querySelector("#messageScroll");
const chatForm = document.querySelector("#chatForm");
const messageInput = document.querySelector("#messageInput");
const sendBtn = document.querySelector("#sendBtn");
const themeToggleBtn = document.querySelector("#themeToggleBtn");
const themeIcon = document.querySelector("#themeIcon");
const sidebar = document.querySelector("#sidebar");
const openSidebarBtn = document.querySelector("#openSidebarBtn");
const closeSidebarBtn = document.querySelector("#closeSidebarBtn");
const newChatBtn = document.querySelector("#newChatBtn");
const oocConfigBtn = document.querySelector("#oocConfigBtn");
const usernotesBtn = document.querySelector("#usernotesBtn");
const profileSelector = document.querySelector("#profileSelector");
const worldDropdown = document.querySelector("#worldDropdown");
const worldTrigger = document.querySelector("#worldTrigger");
const worldLabel = document.querySelector("#worldLabel");
const worldMenu = document.querySelector("#worldMenu");
const scenarioDropdown = document.querySelector("#scenarioDropdown");
const scenarioTrigger = document.querySelector("#scenarioTrigger");
const scenarioLabel = document.querySelector("#scenarioLabel");
const scenarioMenu = document.querySelector("#scenarioMenu");
const actorModelDropdown = document.querySelector("#actorModelDropdown");
const actorModelTrigger = document.querySelector("#actorModelTrigger");
const actorModelLabel = document.querySelector("#actorModelLabel");
const actorModelMenu = document.querySelector("#actorModelMenu");
const conversationList = document.querySelector(".conversation-list");
const schemaPanelBtn = document.querySelector("#schemaPanelBtn");
const locationPanelBtn = document.querySelector("#locationPanelBtn");
const inspectorActionButtons = document.querySelectorAll("[data-inspector-action]");
const closeInspectorBtn = document.querySelector("#closeInspectorBtn");
const inspectorPanel = document.querySelector("#inspectorPanel");
const inspectorTitle = document.querySelector("#inspectorTitle");
const inspectorMeta = document.querySelector("#inspectorMeta");
const inspectorBody = document.querySelector("#inspectorBody");
const workspace = document.querySelector(".workspace");
const engineStatus = document.querySelector("#engineStatus");
const engineStatusText = document.querySelector("#engineStatusText");

const STORAGE_KEY = "graphrag-chat-ui-theme";
const ACTOR_MODEL_STORAGE_KEY = "graphrag-chat-ui-actor-model";
const SIDEBAR_COLLAPSED_STORAGE_KEY = "graphrag-chat-ui-sidebar-collapsed";
const FONT_STORAGE_KEY = "graphrag-chat-ui-font";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

const ACTOR_MODELS = [
  { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro Preview", provider: "Gemini" },
  { id: "gemini-3.6-flash", label: "Gemini 3.6 Flash", provider: "Gemini" },
  { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash", provider: "Gemini" },
  { id: "gemini-3-flash-preview", label: "Gemini 3 Flash Preview", provider: "Gemini" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5", provider: "Claude" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", provider: "Claude" },
  { id: "claude-opus-4-6", label: "Claude Opus 4.6", provider: "Claude" },
  { id: "claude-opus-4-7", label: "Claude Opus 4.7", provider: "Claude" },
  { id: "claude-opus-4-8", label: "Claude Opus 4.8", provider: "Claude" },
  { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro", provider: "DeepSeek" }
];
const ACTOR_MODEL_PROVIDER_ORDER = ["Gemini", "Claude", "DeepSeek"];

const DEFAULT_ACTOR_MODEL = "gemini-3.1-pro-preview";
const ACTOR_MODEL_ALIASES = {};

let WORLD_PROFILES = [
  {
    id: "babe_univ",
    label: "babe_univ",
    scenarios: [{ id: "default", label: "default" }]
  },
  {
    id: "milkway_highschool",
    label: "milkway_highschool",
    scenarios: [{ id: "utaite", label: "utaite" }]
  },
  {
    id: "rofan",
    label: "rofan",
    scenarios: [
      { id: "main", label: "main" },
      { id: "academy", label: "academy" }
    ]
  },
  {
    id: "sses",
    label: "sses",
    scenarios: [{ id: "default", label: "default" }]
  },
  {
    id: "sunghwa_high_school",
    label: "sunghwa_high_school",
    scenarios: [
      { id: "default", label: "default" },
      { id: "volleyball_team", label: "volleyball_team" },
      { id: "sleepy_friend", label: "sleepy_friend" },
      { id: "altered", label: "altered" }
    ]
  },
  {
    id: "sunghwa_middleschool",
    label: "sunghwa_middleschool",
    scenarios: [{ id: "default", label: "default" }]
  },
  {
    id: "sunghwa_university",
    label: "sunghwa_university",
    scenarios: [{ id: "default", label: "default" }]
  },
  {
    id: "ts",
    label: "ts",
    scenarios: [{ id: "default", label: "default" }]
  }
];

let currentWorldId = "babe_univ";
let currentScenarioId = "default";
let selectedActorModel = DEFAULT_ACTOR_MODEL;
let messages = [];
let editingMessageId = null;
let editDraft = "";
let isGenerating = false;
let currentThreadId = null;
let conversations = [];
let activeInspector = null;
let schemaTables = [];
let schemaViewerUrl = "";
let schemaSource = "";
let locationBoard = null;
let isSidebarCollapsed = false;
// 진행 중인 스트리밍 응답 취소용 — 생성 중 send 버튼이 중단 버튼으로 동작한다.
let currentAbortController = null;

// OOC 설정 (per-thread; thread 없을 땐 JS 변수로만 유지)
let currentOocConfig = "";
// 유저노트 (per-thread 캐시)
let currentUsernotes = [];
// 유저노트 모달 상태
let usernoteDetailNoteId = null;  // null = 새 노트
let usernoteDetailEnabled = true;

function setEngineStatus(state, label) {
  if (!engineStatus || !engineStatusText) {
    return;
  }

  engineStatus.dataset.state = state;
  engineStatusText.textContent = label;
}

function createId(prefix = "msg") {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function getCurrentTime() {
  const now = new Date();
  return now.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function getActorModelOption(modelId = selectedActorModel) {
  return ACTOR_MODELS.find((model) => model.id === modelId) || ACTOR_MODELS[0];
}

function normalizeActorModel(modelId) {
  return getActorModelOption(ACTOR_MODEL_ALIASES[modelId] || modelId).id;
}

function getActorModelLabel(modelId = selectedActorModel) {
  return getActorModelOption(modelId).label;
}

function getActorModelsByProvider() {
  return ACTOR_MODEL_PROVIDER_ORDER
    .map((provider) => ({
      provider,
      models: ACTOR_MODELS.filter((model) => model.provider === provider)
    }))
    .filter((group) => group.models.length > 0);
}

function applySelectedActorModel(modelId, persist = true) {
  selectedActorModel = normalizeActorModel(modelId);
  if (persist) {
    localStorage.setItem(ACTOR_MODEL_STORAGE_KEY, selectedActorModel);
  }
  if (actorModelLabel) {
    actorModelLabel.textContent = getActorModelLabel(selectedActorModel);
  }
}

function getSceneTimestamp(place = "GraphRAG") {
  const now = new Date();

  return [
    `${now.getFullYear()}년`,
    `${pad2(now.getMonth() + 1)}월`,
    `${pad2(now.getDate())}일`,
    `${WEEKDAYS[now.getDay()]}요일,`,
    `${pad2(now.getHours())}시`,
    `${pad2(now.getMinutes())}분.`,
    place
  ].join(" ");
}

function escapeHTML(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function textToHTML(value = "") {
  return escapeHTML(value).replace(/\n/g, "<br />");
}

function renderInlineMarkdown(value = "") {
  let html = escapeHTML(value);

  const codeSpans = [];
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    const token = `@@CS${codeSpans.length}@@`;
    codeSpans.push(`<code>${code}</code>`);
    return token;
  });

  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  html = html.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
  html = html.replace(/(?<!_)_([^_\n]+)_(?!_)/g, '<em>$1</em>');
  html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  codeSpans.forEach((code, index) => {
    html = html.replace(`@@CS${index}@@`, code);
  });

  return html;
}

function flushParagraph(paragraphLines, blocks) {
  if (!paragraphLines.length) {
    return;
  }

  // 큰따옴표(곧은 " / 굽은 “)로 시작하는 문단은 대사 문단으로 표시 — 대사끼리는 CSS에서 간격을 좁힌다.
  // 모델 출력이 곧은 따옴표를 쓰는 경우가 많아 두 형태를 모두 감지한다.
  const head = paragraphLines.join("\n").trimStart();
  const isDialogue = head.startsWith("“") || head.startsWith('"');
  const cls = isDialogue ? ' class="is-dialogue"' : "";
  blocks.push(`<p${cls}>${paragraphLines.map((line) => renderInlineMarkdown(line)).join("<br />")}</p>`);
  paragraphLines.length = 0;
}

// ── GFM 표 파싱 ────────────────────────────────────────────────
// 헤더 행 다음 줄이 구분선(예: |:---:|---|)이면 표로 처리한다.
function isTableDivider(line = "") {
  const t = line.trim();
  if (!t.includes("-")) {
    return false;
  }
  return /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/.test(t);
}

function parseTableRow(line = "") {
  let t = line.trim();
  if (t.startsWith("|")) {
    t = t.slice(1);
  }
  if (t.endsWith("|")) {
    t = t.slice(0, -1);
  }
  return t.split("|").map((cell) => cell.trim());
}

function parseTableAligns(line = "") {
  return parseTableRow(line).map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return "";
  });
}

function renderTable(header, aligns, rows) {
  const alignAttr = (index) => (aligns[index] ? ` style="text-align:${aligns[index]}"` : "");
  const head = `<tr>${header
    .map((cell, index) => `<th${alignAttr(index)}>${renderInlineMarkdown(cell)}</th>`)
    .join("")}</tr>`;
  const body = rows
    .map((row) => `<tr>${header
      .map((_, index) => `<td${alignAttr(index)}>${renderInlineMarkdown(row[index] ?? "")}</td>`)
      .join("")}</tr>`)
    .join("");
  return `<div class="md-table-wrap"><table class="md-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

function renderMarkdown(value = "") {
  const text = String(value || "").replace(/\r\n/g, "\n");
  const lines = text.split("\n");
  const blocks = [];
  const paragraph = [];

  let inCode = false;
  let codeLang = "";
  let codeLines = [];
  let listType = null;
  let listItems = [];

  function flushList() {
    if (!listType) {
      return;
    }

    const tag = listType === "ol" ? "ol" : "ul";
    blocks.push(`<${tag}>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${tag}>`);
    listType = null;
    listItems = [];
  }

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.replace(/\s+$/, "");

    const codeFence = line.match(/^```([a-zA-Z0-9_-]*)\s*$/);
    if (codeFence) {
      if (inCode) {
        blocks.push(`<pre><code>${escapeHTML(codeLines.join("\n"))}</code></pre>`);
        inCode = false;
        codeLang = "";
        codeLines = [];
      } else {
        flushParagraph(paragraph, blocks);
        flushList();
        inCode = true;
        codeLang = codeFence[1] || "";
        codeLines = [];
      }
      continue;
    }

    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }

    if (!line.trim()) {
      flushParagraph(paragraph, blocks);
      flushList();
      continue;
    }

    // 표: 현재 줄에 파이프가 있고 다음 줄이 구분선이면 표 블록 전체를 소비한다.
    if (line.includes("|") && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      flushParagraph(paragraph, blocks);
      flushList();

      const header = parseTableRow(line);
      const aligns = parseTableAligns(lines[i + 1]);
      const rows = [];
      let j = i + 2;
      while (j < lines.length && lines[j].trim() && lines[j].includes("|")) {
        rows.push(parseTableRow(lines[j]));
        j++;
      }
      blocks.push(renderTable(header, aligns, rows));
      i = j - 1;
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph(paragraph, blocks);
      flushList();
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    if (/^---+$/.test(line.trim())) {
      flushParagraph(paragraph, blocks);
      flushList();
      blocks.push("<hr />");
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph(paragraph, blocks);
      flushList();
      blocks.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    if (unordered) {
      flushParagraph(paragraph, blocks);
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      listItems.push(unordered[1]);
      continue;
    }

    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph(paragraph, blocks);
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      listItems.push(ordered[1]);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  if (inCode) {
    blocks.push(`<pre><code>${escapeHTML(codeLines.join("\n"))}</code></pre>`);
  }

  flushParagraph(paragraph, blocks);
  flushList();

  return `<div class="markdown-body">${blocks.join("") || "<p></p>"}</div>`;
}

function renderOOCLog(value = "") {
  const text = String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "$1")
    .replace(/(?<!_)_([^_\n]+)_(?!_)/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1");

  return `<div class="ooc-log">${escapeHTML(text)}</div>`;
}

function parseOOCBlock(rawOOC = "") {
  const [summaryPart, detailsPart] = rawOOC.split(/\n---+\n/);

  return {
    summary: (summaryPart || "").trim(),
    details: (detailsPart || "").trim()
  };
}

function extractOOCBlock(content) {
  const taggedMatch = content.match(/<ooc>\s*([\s\S]*?)\s*<\/ooc>/i);

  if (taggedMatch) {
    return {
      content: content.replace(taggedMatch[0], "").trim(),
      ooc: parseOOCBlock(taggedMatch[1])
    };
  }

  const headingMatch = content.match(/(?:^|\n)(?:#{1,3}\s*)?(?:OOC|OOC\s*기록|상태\s*기록)\s*:?\s*\n([\s\S]*)$/i);

  if (headingMatch) {
    return {
      content: content.slice(0, headingMatch.index).trim(),
      ooc: parseOOCBlock(headingMatch[1])
    };
  }

  return { content, ooc: null };
}

function parseAssistantOutput(rawContent) {
  let content = String(rawContent || "").trim();
  let analyze = "";
  let ooc = null;

  const analyzeMatch = content.match(/<analyze>\s*([\s\S]*?)\s*<\/analyze>/i);

  if (analyzeMatch) {
    analyze = analyzeMatch[1].trim();
    content = content.replace(analyzeMatch[0], "").trim();
  }

  const oocResult = extractOOCBlock(content);
  content = oocResult.content;
  ooc = oocResult.ooc;

  const metaMatch = content.match(/^\s*\*\*([^*\n]+?)\*\*\s*(?:\n|$)/);
  let meta = null;
  if (metaMatch) {
    meta = metaMatch[1].trim();
    content = content.slice(metaMatch[0].length).trim();
  }

  return {
    analyze,
    meta,
    ooc,
    body: content
  };
}

function makeAssistantPayload({ analyze, place = "GraphRAG", body = "", ooc = null }) {
  const oocBlock = ooc
    ? `
<ooc>
${ooc.summary}
---
${ooc.details || ""}
</ooc>`
    : "";

  return `<analyze>
${analyze}
</analyze>
**${getSceneTimestamp(place)}**
${body}
${oocBlock}`.trim();
}

function createMessageTopline(message, metaText) {
  const topline = document.createElement("div");
  topline.className = "message-topline";

  const name = document.createElement("strong");
  name.textContent = message.role === "user" ? "You" : "GraphRAG";

  const right = document.createElement("time");
  right.textContent = metaText;

  if (message.role === "assistant" && message.actorModel) {
    right.append(document.createTextNode(` · ${getActorModelLabel(message.actorModel)}`));
  }

  if (message.edited) {
    const edited = document.createElement("span");
    edited.className = "edited-mark";
    edited.textContent = "수정됨";
    right.append(document.createTextNode(" · "), edited);
  }

  topline.append(name, right);
  return topline;
}

function createAnalysisBox(analyzeText) {
  const details = document.createElement("details");
  details.className = "analysis-box";

  const summary = document.createElement("summary");
  summary.textContent = "analyze";

  const pre = document.createElement("pre");
  pre.textContent = analyzeText;

  details.append(summary, pre);
  return details;
}

function parseSceneMeta(meta) {
  // 씬 헤더 문자열("…년 …월 …일 …요일, …시 …분, 위치")에서 날짜·시각을 직접 뽑고
  // 남는 부분을 위치로 본다. 구분자(쉼표/마침표/가운뎃점)나 공백 어느 쪽이든 견디게 한다.
  const raw = String(meta || "").trim();
  if (!raw) {
    return null;
  }

  const dateMatch = raw.match(
    /\d{2,4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일(?:\s*,?\s*\(?[월화수목금토일]\s*요일?\)?)?|\d{1,2}\s*월\s*\d{1,2}\s*일(?:\s*,?\s*\(?[월화수목금토일]\s*요일?\)?)?/
  );
  const timeMatch = raw.match(/(?:오전|오후)?\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|\d{1,2}:\d{2}/);

  let place = raw;
  if (dateMatch) place = place.replace(dateMatch[0], "");
  if (timeMatch) place = place.replace(timeMatch[0], "");
  place = place.replace(/[,.·、]/g, " ").replace(/\s+/g, " ").trim();

  const date = dateMatch ? dateMatch[0].trim() : "";
  const time = timeMatch ? timeMatch[0].trim() : "";

  if (!date && !time && !place) {
    return null;
  }
  return { date, time, place };
}

function createSceneRule(position) {
  const rule = document.createElement("div");
  rule.className = `scene-meta-rule ${position}`;
  const notch = document.createElement("span");
  notch.className = "scene-meta-notch";
  rule.appendChild(notch);
  return rule;
}

function createSceneMeta(meta) {
  const parsed = parseSceneMeta(meta);
  if (!parsed) {
    return null;
  }

  const box = document.createElement("div");
  box.className = "scene-meta";

  box.appendChild(createSceneRule("top"));

  // 날짜 줄만 강조하고 시각·위치는 보조 줄로 — 빈 항목은 줄을 만들지 않는다.
  [parsed.date, parsed.time, parsed.place].forEach((value, index) => {
    if (!value) {
      return;
    }
    const line = document.createElement("div");
    line.className = index === 0 ? "scene-meta-date" : "scene-meta-line";
    line.textContent = value;
    box.appendChild(line);
  });

  box.appendChild(createSceneRule("bottom"));
  return box;
}

function createVariantHistory(message) {
  const variants = message.variants || [];
  if (!variants.length) {
    return null;
  }

  const details = document.createElement("details");
  details.className = "variant-history";

  const summary = document.createElement("summary");
  summary.textContent = `이전 응답 ${variants.length}개`;
  details.appendChild(summary);

  variants.forEach((variant, index) => {
    const item = document.createElement("div");
    item.className = "variant-item";

    const header = document.createElement("div");
    header.className = "variant-header";
    header.textContent = [
      `#${variants.length - index}`,
      variant.createdAt || "",
      getActorModelLabel(variant.actorModel || message.actorModel)
    ].filter(Boolean).join(" · ");

    const parsed = parseAssistantOutput(variant.content);
    item.appendChild(header);

    if (parsed.analyze) {
      item.appendChild(createAnalysisBox(parsed.analyze));
    }

    const sceneMeta = createSceneMeta(parsed.meta);
    if (sceneMeta) {
      item.appendChild(sceneMeta);
    }

    const body = document.createElement("div");
    body.className = "variant-body";
    body.innerHTML = renderMarkdown(parsed.body || "응답 내용이 비어 있습니다.");
    item.appendChild(body);

    if (parsed.ooc) {
      item.appendChild(createOOCBlock(parsed.ooc));
    }

    details.appendChild(item);
  });

  return details;
}

function createVariantNavigator(message) {
  const versions = getAllVersions(message);
  if (versions.length <= 1) {
    return null;
  }

  const idx = message.activeVersionIdx ?? versions.length - 1;

  const nav = document.createElement("div");
  nav.className = "variant-nav";

  const prev = document.createElement("button");
  prev.className = "variant-nav-btn";
  prev.type = "button";
  prev.textContent = "‹";
  prev.disabled = idx === 0 || isGenerating;
  prev.addEventListener("click", () => void navigateVariant(message.id, idx - 1));

  const counter = document.createElement("span");
  counter.className = "variant-nav-counter";
  counter.textContent = `${idx + 1} / ${versions.length}`;

  const next = document.createElement("button");
  next.className = "variant-nav-btn";
  next.type = "button";
  next.textContent = "›";
  next.disabled = idx === versions.length - 1 || isGenerating;
  next.addEventListener("click", () => void navigateVariant(message.id, idx + 1));

  nav.append(prev, counter, next);
  return nav;
}

async function navigateVariant(messageId, newIdx) {
  const msgIdx = messages.findIndex((m) => m.id === messageId);
  if (msgIdx < 0) {
    return;
  }

  const msg = messages[msgIdx];
  const versions = getAllVersions(msg);
  const currentIdx = msg.activeVersionIdx ?? versions.length - 1;

  if (newIdx === currentIdx || newIdx < 0 || newIdx >= versions.length) {
    return;
  }

  messages[msgIdx] = { ...msg, activeVersionIdx: newIdx };
  renderMessages();

  if (!currentThreadId) {
    return;
  }

  try {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/messages/${encodeURIComponent(messageId)}/variants/activate`,
      {
        method: "PATCH",
        body: JSON.stringify({ version_index: newIdx })
      }
    );
    const updated = normalizeServerMessage(result.message);
    const newVersions = getAllVersions(updated);
    messages[msgIdx] = { ...updated, activeVersionIdx: newVersions.length - 1 };
    renderMessages();
  } catch (error) {
    messages[msgIdx] = { ...msg, activeVersionIdx: currentIdx };
    renderMessages();
    console.error("variant navigate failed:", error);
  }
}

function createOOCBlock(ooc) {
  const details = document.createElement("details");
  details.className = "ooc-block";

  const summary = document.createElement("summary");

  const left = document.createElement("span");
  left.className = "ooc-summary-left";

  const badge = document.createElement("span");
  badge.className = "ooc-badge";
  badge.textContent = "OOC 기록";

  left.appendChild(badge);

  const count = document.createElement("span");
  count.className = "ooc-count";
  const detailLines = [ooc.summary, ooc.details]
    .filter(Boolean)
    .join("\n")
    .split("\n")
    .filter((line) => line.trim()).length;
  count.textContent = `${detailLines || 1} lines`;

  summary.append(left, count);

  const body = document.createElement("div");
  body.className = "ooc-block-body";

  const content = [ooc.summary, ooc.details].filter(Boolean).join("\n");
  body.innerHTML = renderOOCLog(content || "기록된 OOC 변경사항이 없습니다.");

  details.append(summary, body);
  return details;
}

function createTypingContent() {
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.setAttribute("aria-label", "응답 작성 중");
  typing.innerHTML = "<span></span><span></span><span></span>";
  return typing;
}

function autoResizeEditTextarea(textarea) {
  textarea.style.height = "0px";
  textarea.style.height = `${textarea.scrollHeight}px`;
}

function createEditPanel(message) {
  const panel = document.createElement("div");
  panel.className = "edit-panel";

  const textarea = document.createElement("textarea");
  textarea.className = "edit-textarea";
  textarea.value = editDraft;
  textarea.addEventListener("input", (event) => {
    editDraft = event.target.value;
    autoResizeEditTextarea(textarea);
  });

  const help = document.createElement("p");
  help.className = "edit-help";
  help.textContent = message.role === "user"
    ? "유저 메시지를 저장하면 연결된 assistant 응답을 다시 생성합니다."
    : "assistant 메시지는 원문 포맷 그대로 수정합니다. <analyze>, <ooc>, 시간·장소 줄도 직접 편집할 수 있습니다.";

  const actions = document.createElement("div");
  actions.className = "edit-actions";

  const cancel = document.createElement("button");
  cancel.className = "edit-button cancel";
  cancel.type = "button";
  cancel.textContent = "취소";
  cancel.addEventListener("click", cancelEdit);

  const save = document.createElement("button");
  save.className = "edit-button save";
  save.type = "button";
  save.textContent = message.role === "user" ? "저장 후 재생성" : "저장";
  save.addEventListener("click", () => saveEdit(message.id));

  actions.append(cancel, save);
  panel.append(textarea, help, actions);

  setTimeout(() => {
    autoResizeEditTextarea(textarea);
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }, 0);

  return panel;
}

function createMessageActions(message) {
  const actions = document.createElement("div");
  actions.className = "message-actions";

  if (message.localOnly) {
    return actions;
  }

  if (message.role === "assistant" && !message.typing && message.parentUserId) {
    const modelSelect = document.createElement("select");
    modelSelect.className = "message-model-select";
    modelSelect.disabled = isGenerating;
    modelSelect.setAttribute("aria-label", "리롤 모델 선택");
    getActorModelsByProvider().forEach((group) => {
      const optgroup = document.createElement("optgroup");
      optgroup.label = group.provider;
      group.models.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.label;
        optgroup.appendChild(option);
      });
      modelSelect.appendChild(optgroup);
    });
    modelSelect.value = normalizeActorModel(message.actorModel || selectedActorModel);
    actions.appendChild(modelSelect);

    const reroll = document.createElement("button");
    reroll.className = "message-action";
    reroll.type = "button";
    reroll.textContent = "↻ 리롤";
    reroll.disabled = isGenerating;
    reroll.addEventListener("click", () => rerollAssistant(message.id, modelSelect.value));
    actions.appendChild(reroll);
  }

  if (!message.typing) {
    const edit = document.createElement("button");
    edit.className = "message-action";
    edit.type = "button";
    edit.textContent = "✎ 수정";
    edit.disabled = isGenerating;
    edit.addEventListener("click", () => startEdit(message.id));
    actions.appendChild(edit);

    const remove = document.createElement("button");
    remove.className = "message-action danger";
    remove.type = "button";
    remove.textContent = "🗑 삭제";
    remove.disabled = isGenerating;
    remove.addEventListener("click", () => deleteMessage(message.id));
    actions.appendChild(remove);
  }

  return actions;
}

function renderAssistantCard(card, message) {
  if (message.typing) {
    card.appendChild(createMessageTopline(message, "응답 생성 중"));
    card.appendChild(createTypingContent());
    return;
  }

  const versions = getAllVersions(message);
  const activeIdx = message.activeVersionIdx ?? versions.length - 1;
  const activeVersion = versions[Math.min(activeIdx, versions.length - 1)];
  const displayContent = activeVersion.content;
  const displayActorModel = activeVersion.actorModel ?? message.actorModel;

  const parsed = parseAssistantOutput(displayContent);

  // 스트리밍 중 본문이 아직 도착하지 않은 상태(헤더만 수신 등)면 typing indicator를 유지한다.
  if (message.streaming && !parsed.body) {
    card.appendChild(createMessageTopline(message, "응답 생성 중"));
    card.appendChild(createTypingContent());
    return;
  }

  // 씬 날짜·시각·위치는 본문 안 배지로 옮기므로 상단 우측엔 실제 생성 시각·모델만 남긴다.
  const toplineMsg = { ...message, content: displayContent, actorModel: displayActorModel };
  card.appendChild(createMessageTopline(toplineMsg, message.createdAt));

  if (editingMessageId === message.id) {
    card.appendChild(createEditPanel(message));
    return;
  }

  if (parsed.analyze) {
    card.appendChild(createAnalysisBox(parsed.analyze));
  }

  const sceneMeta = createSceneMeta(parsed.meta);
  if (sceneMeta) {
    card.appendChild(sceneMeta);
  }

  const body = document.createElement("div");
  body.innerHTML = renderMarkdown(parsed.body || "응답 내용이 비어 있습니다.");
  card.appendChild(body);

  if (parsed.ooc) {
    card.appendChild(createOOCBlock(parsed.ooc));
  }

  const nav = createVariantNavigator(message);
  if (nav) {
    card.appendChild(nav);
  }
}

function createOOCInputBlock(oocConfig) {
  const details = document.createElement("details");
  details.className = "ooc-input-block";

  const summary = document.createElement("summary");
  summary.textContent = "OOC INPUT";

  const body = document.createElement("div");
  body.className = "ooc-input-body";
  body.textContent = oocConfig;

  details.append(summary, body);
  return details;
}

function renderUserCard(card, message) {
  card.appendChild(createMessageTopline(message, message.createdAt));

  if (editingMessageId === message.id) {
    card.appendChild(createEditPanel(message));
    return;
  }

  const body = document.createElement("div");
  body.innerHTML = renderMarkdown(message.content);
  card.appendChild(body);

  if (message.oocConfig) {
    card.appendChild(createOOCInputBlock(message.oocConfig));
  }
}

function renderMessages() {
  const wasNearBottom = messageScroll.scrollHeight - messageScroll.scrollTop - messageScroll.clientHeight < 120;

  messageScroll.innerHTML = `
    <div class="date-divider">
      <span>오늘</span>
    </div>
  `;

  messages.forEach((message) => {
    const row = document.createElement("article");
    row.className = `message-row ${message.role}`;
    row.dataset.messageId = message.id;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = message.role === "user" ? "나" : "G";

    const wrap = document.createElement("div");
    wrap.className = "message-wrap";

    const card = document.createElement("div");
    card.className = "message-card";

    if (message.role === "assistant") {
      renderAssistantCard(card, message);
    } else {
      renderUserCard(card, message);
    }

    wrap.appendChild(card);
    wrap.appendChild(createMessageActions(message));

    if (message.role === "user") {
      row.append(wrap, avatar);
    } else {
      row.append(avatar, wrap);
    }

    messageScroll.appendChild(row);
  });

  if (wasNearBottom) {
    scrollToBottom();
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messageScroll.scrollTop = messageScroll.scrollHeight;
  });
}

function isScrollNearBottom() {
  return messageScroll.scrollHeight - messageScroll.scrollTop - messageScroll.clientHeight < 120;
}

// 단일 메시지 row만 다시 그린다 — 스트리밍 토큰마다 전체를 재렌더하지 않기 위한 경량 경로.
// 해당 row를 찾지 못하면 전체 렌더로 안전하게 폴백한다.
function rerenderMessageRow(messageId) {
  const message = messages.find((item) => item.id === messageId);
  if (!message) {
    return;
  }

  const row = messageScroll.querySelector(`.message-row[data-message-id="${CSS.escape(messageId)}"]`);
  const wrap = row?.querySelector(".message-wrap");
  if (!wrap) {
    renderMessages();
    return;
  }

  const card = document.createElement("div");
  card.className = "message-card";
  if (message.role === "assistant") {
    renderAssistantCard(card, message);
  } else {
    renderUserCard(card, message);
  }
  wrap.replaceChildren(card, createMessageActions(message));
}

function autoResizeTextarea() {
  messageInput.style.height = "auto";
  messageInput.style.height = `${messageInput.scrollHeight}px`;
}

function createLoadingStatus(text, subtext = "응답을 준비하는 중입니다.") {
  const wrapper = document.createElement("div");
  wrapper.className = "loading-status";

  const spinner = document.createElement("span");
  spinner.className = "spinner";
  spinner.setAttribute("aria-hidden", "true");

  const textBlock = document.createElement("span");
  textBlock.innerHTML = subtext
    ? `<span>${escapeHTML(text)}</span><span class="loading-subtext">${escapeHTML(subtext)}</span>`
    : `<span>${escapeHTML(text)}</span>`;

  wrapper.append(spinner, textBlock);
  return wrapper;
}

function showLoadingToast(text) {
  const toast = document.createElement("div");
  toast.className = "loading-toast";
  toast.appendChild(createLoadingStatus(text, ""));

  document.body.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.add("show");
  });

  return () => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 180);
  };
}

// 비차단 알림 토스트. 로딩 토스트와 같은 위치·애니메이션을 쓰되 일정 시간 뒤 자동 소멸한다.
// 차단형 alert() 대체용 — 에러/검증 실패를 흐름을 막지 않고 표시한다.
function showToast(text, { duration = 3200, variant = "error" } = {}) {
  const toast = document.createElement("div");
  toast.className = `app-toast ${variant}`;
  toast.textContent = text;
  document.body.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add("show"));
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 180);
  }, duration);
}

async function apiFetchJSON(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }

  return response.json();
}

function normalizeServerMessage(message) {
  const variants = (message.variants || []).map((variant) => ({
    id: variant.id || createId("variant"),
    content: variant.content || "",
    createdAt: variant.createdAt || getCurrentTime(),
    actorModel: variant.actorModel || null,
    edited: Boolean(variant.edited)
  }));
  return {
    id: message.id,
    role: message.role,
    content: message.content || "",
    createdAt: message.createdAt || getCurrentTime(),
    parentUserId: message.parentUserId || null,
    actorModel: message.actorModel || null,
    oocConfig: message.oocConfig || "",
    variants,
    activeVersionIdx: variants.length,
    edited: Boolean(message.edited),
    typing: false
  };
}

function getAllVersions(message) {
  const variantsOldestFirst = (message.variants || []).slice().reverse();
  return [
    ...variantsOldestFirst,
    { content: message.content, createdAt: message.createdAt, actorModel: message.actorModel }
  ];
}

function makeStartMessage(openingScene = "") {
  const body = openingScene || `## ${getConversationLabel()}\n\n메시지를 입력하면 이 세계관과 시나리오로 새 대화를 시작합니다.`;
  return {
    id: "local_start",
    role: "assistant",
    content: body,
    createdAt: getCurrentTime(),
    edited: false,
    variants: [],
    localOnly: true
  };
}

async function loadOpeningScene() {
  const params = new URLSearchParams({
    world_id: currentWorldId,
    scenario_id: currentScenarioId
  });
  const payload = await apiFetchJSON(`/api/opening-scene?${params.toString()}`);
  return payload.opening_scene || "";
}

async function showStartMessage() {
  let openingScene = "";
  try {
    openingScene = await loadOpeningScene();
  } catch (error) {
    console.error(error);
  }

  messages = [makeStartMessage(openingScene)];
  renderMessages();
  updateConversationInfo("새 대화");
  renderConversationList();
}

function applyConversationPayload(payload) {
  currentThreadId = payload.thread_id;
  currentWorldId = payload.world_id;
  currentScenarioId = payload.scenario_id || "default";
  applySelectedActorModel(payload.actor_model || selectedActorModel);
  currentOocConfig = payload.ooc_config || "";
  currentUsernotes = Array.isArray(payload.usernotes) ? payload.usernotes : [];
  messages = (payload.messages || []).map(normalizeServerMessage);
  if (!messages.length) {
    messages = [makeStartMessage()];
  }
  renderProfileDropdown();
  renderMessages();
  updateConversationInfo(payload.preview || null);
  renderConversationList();
}

async function ensureConversation() {
  if (currentThreadId) {
    return currentThreadId;
  }

  const payload = await apiFetchJSON("/api/conversations", {
    method: "POST",
    body: JSON.stringify({
      world_id: currentWorldId,
      scenario_id: currentScenarioId,
      actor_model: selectedActorModel,
      ooc_config: currentOocConfig
    })
  });
  currentThreadId = payload.thread_id;
  currentWorldId = payload.world_id;
  currentScenarioId = payload.scenario_id || "default";
  applySelectedActorModel(payload.actor_model || selectedActorModel);
  if (!messages.length || messages.every((message) => message.localOnly)) {
    applyConversationPayload(payload);
  } else {
    renderConversationList();
  }
  return currentThreadId;
}

async function loadWorldProfiles() {
  try {
    const payload = await apiFetchJSON("/api/worlds");
    if (Array.isArray(payload.worlds) && payload.worlds.length) {
      WORLD_PROFILES = payload.worlds;
      if (!WORLD_PROFILES.some((world) => world.id === currentWorldId)) {
        currentWorldId = WORLD_PROFILES[0].id;
        currentScenarioId = WORLD_PROFILES[0].scenarios?.[0]?.id || "default";
      }
    }
  } catch (error) {
    // 서버 목록 조회 실패 시 하드코딩 폴백을 유지하되, 목록이 최신이 아닐 수 있음을 사용자에게 알린다.
    console.error(error);
    showToast("세계관 목록을 불러오지 못했습니다. 기본 목록을 표시합니다.");
  }
}

async function loadConversationList() {
  const payload = await apiFetchJSON("/api/conversations");
  conversations = payload.conversations || [];
  renderConversationList();
}

function renderConversationList() {
  if (!conversationList) {
    return;
  }

  const visibleConversations = conversations.length
    ? conversations
    : (currentThreadId ? [{
      thread_id: currentThreadId,
      title: getConversationLabel(),
      preview: "새 대화",
      world_id: currentWorldId,
      scenario_id: currentScenarioId
    }] : []);

  conversationList.innerHTML = visibleConversations.map((conversation) => {
    const isActive = conversation.thread_id === currentThreadId;
    const scenarioId = conversation.scenario_id || "default";
    const scenarioLabel = getScenarioLabelFor(conversation.world_id, scenarioId);
    const title = scenarioLabel;
    return `
      <button class="conversation-item ${isActive ? "active" : ""}" data-thread-id="${escapeHTML(conversation.thread_id)}">
        <span class="conversation-title">${escapeHTML(title)}</span>
        <span class="conversation-meta">${escapeHTML(conversation.preview || "새 대화")}</span>
      </button>
    `;
  }).join("");

  conversationList.querySelectorAll(".conversation-item").forEach((item) => {
    item.addEventListener("click", async () => {
      if (isGenerating) {
        return;
      }
      const threadId = item.dataset.threadId;
      if (!threadId || threadId === currentThreadId) {
        closeSidebarOnMobile();
        return;
      }
      try {
        const payload = await apiFetchJSON(`/api/conversations/${encodeURIComponent(threadId)}`);
        applyConversationPayload(payload);
        await refreshActiveInspector();
        closeSidebarOnMobile();
        scrollToBottom();
      } catch (error) {
        console.error(error);
        showToast("대화를 불러오지 못했습니다.");
      }
    });
  });
}

function setInspectorLoading(title, meta = "불러오는 중") {
  if (!workspace || !inspectorBody) {
    return;
  }

  activeInspector = title === "위치" ? "locations" : "schema";
  workspace.classList.add("inspector-open");
  schemaPanelBtn?.classList.toggle("active", activeInspector === "schema");
  locationPanelBtn?.classList.toggle("active", activeInspector === "locations");
  inspectorActionButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.inspectorAction === activeInspector);
  });
  if (inspectorTitle) {
    inspectorTitle.textContent = title;
  }
  if (inspectorMeta) {
    inspectorMeta.textContent = meta;
  }
  inspectorBody.innerHTML = `<div class="inspector-loading">${escapeHTML(meta)}</div>`;
}

function closeInspector() {
  activeInspector = null;
  workspace?.classList.remove("inspector-open");
  schemaPanelBtn?.classList.remove("active");
  locationPanelBtn?.classList.remove("active");
  inspectorActionButtons.forEach((button) => button.classList.remove("active"));
}

function renderSchemaPanel() {
  if (!inspectorBody) {
    return;
  }

  if (inspectorTitle) {
    inspectorTitle.textContent = "스키마";
  }
  if (inspectorMeta) {
    inspectorMeta.textContent = `${schemaTables.length}개 테이블${schemaSource ? ` · ${schemaSource}` : ""}`;
  }
  if (!schemaTables.length) {
    inspectorBody.innerHTML = `<div class="inspector-empty">표시할 스키마가 없습니다.</div>`;
    return;
  }

  const viewerLink = schemaViewerUrl
    ? `<a class="schema-viewer-link" href="${escapeHTML(schemaViewerUrl)}" target="_blank" rel="noopener noreferrer">스키마 뷰어 열기</a>`
    : "";

  inspectorBody.innerHTML = `
    ${viewerLink}
    ${schemaTables.map((table) => `
    <details class="schema-table" open>
      <summary>
        <span>${escapeHTML(table.name)}</span>
        <em class="schema-badge">${escapeHTML(table.type || "table")}</em>
      </summary>
      <div class="schema-columns">
        ${(table.columns || []).map((column) => `
          <div class="schema-column">
            <strong>${escapeHTML(column.name)}</strong>
            <span>${escapeHTML(column.type)}</span>
          </div>
        `).join("") || `<div class="schema-column"><strong>컬럼 없음</strong><span></span></div>`}
      </div>
    </details>
  `).join("")}`;
}

function charactersForLocation(locationId) {
  return (locationBoard?.characters || []).filter((character) => character.location_id === locationId);
}

function renderCharacterChip(character) {
  const meta = [character.type, character.mood, character.current_task].filter(Boolean).join(" · ");
  return `
    <button
      class="character-chip"
      type="button"
      draggable="true"
      data-character-id="${escapeHTML(character.id)}"
      title="${escapeHTML(character.id)}"
    >
      <strong>${escapeHTML(character.name)}</strong>
      <span class="character-meta">${escapeHTML(meta || character.id)}</span>
    </button>
  `;
}

function renderLocationPanel() {
  if (!inspectorBody) {
    return;
  }

  const locations = locationBoard?.locations || [];
  const characters = locationBoard?.characters || [];
  if (inspectorTitle) {
    inspectorTitle.textContent = "위치";
  }
  if (inspectorMeta) {
    inspectorMeta.textContent = `${characters.length}명 · ${locations.length}개 장소`;
  }
  if (!locations.length) {
    inspectorBody.innerHTML = `<div class="inspector-empty">표시할 위치 정보가 없습니다.</div>`;
    return;
  }

  inspectorBody.innerHTML = `
    <div class="location-board">
      ${locations.map((location) => {
        const chars = charactersForLocation(location.id);
        const currentMark = location.id === locationBoard?.current_location_id ? "현재" : "";
        return `
          <section class="location-column" data-location-id="${escapeHTML(location.id)}">
            <div class="location-title">
              <strong>${escapeHTML(location.name || location.id)}</strong>
              <span>${escapeHTML(currentMark || location.id)}</span>
            </div>
            ${location.summary ? `<div class="location-summary">${escapeHTML(location.summary)}</div>` : ""}
            <div class="location-characters">
              ${chars.map(renderCharacterChip).join("") || `<div class="character-meta">배치된 캐릭터 없음</div>`}
            </div>
          </section>
        `;
      }).join("")}
    </div>
  `;

  bindLocationDragEvents();
}

function bindLocationDragEvents() {
  inspectorBody?.querySelectorAll(".character-chip").forEach((chip) => {
    chip.addEventListener("dragstart", (event) => {
      event.dataTransfer?.setData("text/plain", chip.dataset.characterId || "");
      event.dataTransfer?.setDragImage(chip, 12, 12);
    });
  });

  inspectorBody?.querySelectorAll(".location-column").forEach((column) => {
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      column.classList.add("drop-target");
    });
    column.addEventListener("dragleave", () => {
      column.classList.remove("drop-target");
    });
    column.addEventListener("drop", async (event) => {
      event.preventDefault();
      column.classList.remove("drop-target");
      const characterId = event.dataTransfer?.getData("text/plain");
      const locationId = column.dataset.locationId;
      if (!characterId || !locationId) {
        return;
      }
      await moveCharacterToLocation(characterId, locationId);
    });
  });
}

async function openSchemaInspector() {
  setInspectorLoading("스키마", "스키마를 불러오는 중");
  try {
    const threadId = await ensureConversation();
    const payload = await apiFetchJSON(`/api/conversations/${encodeURIComponent(threadId)}/schema`);
    schemaTables = payload.schema || [];
    schemaViewerUrl = payload.viewer_url || "";
    schemaSource = payload.source || "";
    renderSchemaPanel();
  } catch (error) {
    console.error(error);
    inspectorBody.innerHTML = `<div class="inspector-empty">스키마를 불러오지 못했습니다.</div>`;
  }
}

async function openLocationInspector() {
  setInspectorLoading("위치", "위치 정보를 불러오는 중");
  try {
    const threadId = await ensureConversation();
    locationBoard = await apiFetchJSON(`/api/conversations/${encodeURIComponent(threadId)}/locations`);
    renderLocationPanel();
  } catch (error) {
    console.error(error);
    inspectorBody.innerHTML = `<div class="inspector-empty">위치 정보를 불러오지 못했습니다.</div>`;
  }
}

async function refreshActiveInspector() {
  if (activeInspector === "schema") {
    await openSchemaInspector();
  } else if (activeInspector === "locations") {
    await openLocationInspector();
  }
}

async function moveCharacterToLocation(characterId, locationId) {
  if (isGenerating) {
    return;
  }

  const previousBoard = locationBoard;
  const nextCharacters = (locationBoard?.characters || []).map((character) => (
    character.id === characterId
      ? { ...character, location_id: locationId, location_name: locationId }
      : character
  ));
  locationBoard = { ...(locationBoard || {}), characters: nextCharacters };
  renderLocationPanel();

  try {
    const threadId = await ensureConversation();
    locationBoard = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(threadId)}/locations/move`,
      {
        method: "PATCH",
        body: JSON.stringify({
          character_id: characterId,
          location_id: locationId
        })
      }
    );
    renderLocationPanel();
  } catch (error) {
    console.error(error);
    locationBoard = previousBoard;
    renderLocationPanel();
    showToast("캐릭터 위치를 변경하지 못했습니다.");
  }
}

async function readNDJSONStream(response, onEvent) {
  if (!response.body) {
    throw new Error("브라우저가 fetch streaming을 지원하지 않습니다.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed) {
        onEvent(JSON.parse(trimmed));
      }
    }
  }

  const tail = buffer.trim();
  if (tail) {
    onEvent(JSON.parse(tail));
  }
}

async function requestAssistantReply(userMessage, mode = "normal") {
  const streamOptions = typeof mode === "object" && mode ? mode : {};
  const threadId = await ensureConversation();
  let finalMessage = null;

  const controller = new AbortController();
  currentAbortController = controller;

  const response = await fetch(`/api/conversations/${encodeURIComponent(threadId)}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      content: userMessage,
      client_message_id: streamOptions.clientMessageId || null,
      actor_model: selectedActorModel
    }),
    signal: controller.signal
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }

  await readNDJSONStream(response, (event) => {
    if (event.type === "error") {
      throw new Error(event.content || "응답 생성 중 오류가 발생했습니다.");
    }
    if (event.type === "status" && typeof streamOptions.onStatus === "function") {
      streamOptions.onStatus(event.content);
    }
    if (event.type === "token" && typeof streamOptions.onToken === "function") {
      streamOptions.onToken(event.content || "");
    }
    if (event.type === "complete" && event.message) {
      finalMessage = normalizeServerMessage(event.message);
      updateConversationInfo(event.preview || finalMessage.content);
    }
  });

  if (!finalMessage) {
    throw new Error("완료된 assistant 메시지를 받지 못했습니다.");
  }

  return finalMessage;
}

function getCurrentWorld() {
  return WORLD_PROFILES.find((world) => world.id === currentWorldId) || WORLD_PROFILES[0];
}

function getCurrentScenario() {
  const world = getCurrentWorld();
  return world.scenarios.find((scenario) => scenario.id === currentScenarioId) || world.scenarios[0];
}

function getConversationLabel() {
  const world = getCurrentWorld();
  const scenario = getCurrentScenario();
  return `${world.label}/${scenario.label || scenario.id}`;
}

function getScenarioLabelFor(worldId, scenarioId) {
  const w = WORLD_PROFILES.find((p) => p.id === worldId);
  const s = w?.scenarios?.find((sc) => sc.id === scenarioId);
  return s?.label || scenarioId;
}

function getAllProfiles() {
  return WORLD_PROFILES.flatMap((world) =>
    world.scenarios.map((scenario) => ({
      worldId: world.id,
      scenarioId: scenario.id,
      label: `${world.label}/${scenario.label}`
    }))
  );
}

function updateConversationInfo(previewSource = null) {
  if (worldLabel) {
    worldLabel.textContent = getCurrentWorld().label;
  }

  if (scenarioLabel) {
    scenarioLabel.textContent = getCurrentScenario().id;
  }
}

function renderWorldDropdown() {
  if (!worldMenu || !worldLabel) {
    return;
  }

  worldLabel.textContent = getCurrentWorld().label;

  worldMenu.innerHTML = WORLD_PROFILES
    .map((world) => `
      <button
        class="profile-option ${world.id === currentWorldId ? "active" : ""}"
        type="button"
        role="option"
        data-world-id="${world.id}"
      >${world.label}</button>
    `)
    .join("");

  worldMenu.querySelectorAll(".profile-option").forEach((button) => {
    button.addEventListener("click", () => {
      const nextWorldId = button.dataset.worldId;
      closeAllProfileDropdowns();

      if (nextWorldId === currentWorldId) {
        return;
      }

      currentWorldId = nextWorldId;
      currentScenarioId = getCurrentWorld().scenarios[0]?.id || "default";
      void resetChatForSelectedScenario();
    });
  });
}

function renderScenarioDropdown() {
  if (!scenarioMenu || !scenarioLabel) {
    return;
  }

  const world = getCurrentWorld();

  if (!world.scenarios.some((scenario) => scenario.id === currentScenarioId)) {
    currentScenarioId = world.scenarios[0]?.id || "default";
  }

  const currentScenario = getCurrentScenario();
  scenarioLabel.textContent = currentScenario.id;

  scenarioMenu.innerHTML = world.scenarios
    .map((scenario) => `
      <button
        class="profile-option ${scenario.id === currentScenarioId ? "active" : ""}"
        type="button"
        role="option"
        data-scenario-id="${scenario.id}"
      >${scenario.id}</button>
    `)
    .join("");

  scenarioMenu.querySelectorAll(".profile-option").forEach((button) => {
    button.addEventListener("click", () => {
      const nextScenarioId = button.dataset.scenarioId;
      closeAllProfileDropdowns();

      if (nextScenarioId === currentScenarioId) {
        return;
      }

      currentScenarioId = nextScenarioId;
      void resetChatForSelectedScenario();
    });
  });
}

function renderActorModelDropdown() {
  if (!actorModelMenu || !actorModelLabel) {
    return;
  }

  actorModelLabel.textContent = getActorModelLabel(selectedActorModel);

  actorModelMenu.innerHTML = getActorModelsByProvider()
    .map((group) => `
      <div class="model-provider-label">${group.provider}</div>
      ${group.models
        .map((model) => `
          <button
            class="profile-option ${model.id === selectedActorModel ? "active" : ""}"
            type="button"
            role="option"
            data-actor-model="${model.id}"
          >${model.label}</button>
        `)
        .join("")}
    `)
    .join("");

  actorModelMenu.querySelectorAll(".profile-option").forEach((button) => {
    button.addEventListener("click", () => {
      closeAllProfileDropdowns();
      applySelectedActorModel(button.dataset.actorModel);
    });
  });
}

function renderProfileDropdown() {
  renderWorldDropdown();
  renderScenarioDropdown();
  renderActorModelDropdown();
}

function openWorldDropdown() {
  renderWorldDropdown();
  scenarioDropdown?.classList.remove("open");
  scenarioTrigger?.setAttribute("aria-expanded", "false");
  actorModelDropdown?.classList.remove("open");
  actorModelTrigger?.setAttribute("aria-expanded", "false");
  worldDropdown?.classList.add("open");
  worldTrigger?.setAttribute("aria-expanded", "true");
}

function openScenarioDropdown() {
  renderScenarioDropdown();
  worldDropdown?.classList.remove("open");
  worldTrigger?.setAttribute("aria-expanded", "false");
  actorModelDropdown?.classList.remove("open");
  actorModelTrigger?.setAttribute("aria-expanded", "false");
  scenarioDropdown?.classList.add("open");
  scenarioTrigger?.setAttribute("aria-expanded", "true");
}

function openActorModelDropdown() {
  renderActorModelDropdown();
  worldDropdown?.classList.remove("open");
  worldTrigger?.setAttribute("aria-expanded", "false");
  scenarioDropdown?.classList.remove("open");
  scenarioTrigger?.setAttribute("aria-expanded", "false");
  actorModelDropdown?.classList.add("open");
  actorModelTrigger?.setAttribute("aria-expanded", "true");
}

function closeAllProfileDropdowns() {
  worldDropdown?.classList.remove("open");
  scenarioDropdown?.classList.remove("open");
  actorModelDropdown?.classList.remove("open");
  worldTrigger?.setAttribute("aria-expanded", "false");
  scenarioTrigger?.setAttribute("aria-expanded", "false");
  actorModelTrigger?.setAttribute("aria-expanded", "false");
}

function toggleWorldDropdown() {
  if (worldDropdown?.classList.contains("open")) {
    closeAllProfileDropdowns();
  } else {
    openWorldDropdown();
  }
}

function toggleScenarioDropdown() {
  if (scenarioDropdown?.classList.contains("open")) {
    closeAllProfileDropdowns();
  } else {
    openScenarioDropdown();
  }
}

function toggleActorModelDropdown() {
  if (actorModelDropdown?.classList.contains("open")) {
    closeAllProfileDropdowns();
  } else {
    openActorModelDropdown();
  }
}

async function resetChatForSelectedScenario() {
  editingMessageId = null;
  editDraft = "";
  currentThreadId = null;
  currentOocConfig = "";
  currentUsernotes = [];
  closeInspector();
  await showStartMessage();
  scrollToBottom();
}


function addUserMessage(content) {
  const message = {
    id: createId("user"),
    role: "user",
    content,
    createdAt: getCurrentTime(),
    oocConfig: currentOocConfig,
    edited: false
  };

  messages.push(message);
  renderMessages();
  scrollToBottom();

  return message;
}

function addTypingAssistant(parentUserId) {
  const message = {
    id: createId("typing"),
    role: "assistant",
    content: "",
    createdAt: getCurrentTime(),
    parentUserId,
    actorModel: selectedActorModel,
    typing: true,
    edited: false,
    variants: []
  };

  messages.push(message);
  renderMessages();
  scrollToBottom();

  return message;
}

function replaceTypingWithAssistant(typingId, content, parentUserId) {
  const index = messages.findIndex((message) => message.id === typingId);
  const serverMessage = content && typeof content === "object" ? content : null;

  const assistantMessage = {
    id: serverMessage?.id || createId("assistant"),
    role: "assistant",
    content: serverMessage?.content ?? content,
    createdAt: serverMessage?.createdAt || getCurrentTime(),
    parentUserId: serverMessage?.parentUserId || parentUserId,
    actorModel: serverMessage?.actorModel || selectedActorModel,
    variants: serverMessage?.variants || [],
    edited: Boolean(serverMessage?.edited),
    typing: false
  };

  if (index >= 0) {
    messages.splice(index, 1, assistantMessage);
  } else {
    messages.push(assistantMessage);
  }

  renderMessages();
  updateConversationInfo(content);
  scrollToBottom();

  return assistantMessage;
}

async function runDatabaseTool(toolName, statusText) {
  if (!toolName || isGenerating) {
    return;
  }

  isGenerating = true;
  sendBtn.disabled = true;
  editingMessageId = null;
  editDraft = "";

  const hideLoadingToast = showLoadingToast(statusText || "데이터베이스를 조회하는 중입니다...");
  const typing = addTypingAssistant(null);

  try {
    const threadId = await ensureConversation();
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(threadId)}/tools/${encodeURIComponent(toolName)}`,
      { method: "POST" }
    );
    replaceTypingWithAssistant(typing.id, normalizeServerMessage(result.message), null);
    await loadConversationList();
    updateConversationInfo(result.preview || result.message?.content || null);
  } catch (error) {
    replaceTypingWithAssistant(
      typing.id,
      makeAssistantPayload({
        place: "도구 오류",
        analyze: "DB 도구 실행 중 오류가 발생했다.",
        body: "데이터베이스 도구 결과를 가져오지 못했습니다."
      }),
      null
    );
    console.error(error);
  } finally {
    hideLoadingToast();
    isGenerating = false;
    sendBtn.disabled = false;
    renderMessages();
    scrollToBottom();
  }
}

// 생성 중에는 send 버튼을 중단(■) 버튼으로 토글한다 — 클릭 시 진행 중 스트림을 abort.
function setSendStopMode(on) {
  if (!sendBtn) {
    return;
  }
  sendBtn.classList.toggle("stopping", on);
  sendBtn.disabled = false;
  sendBtn.setAttribute("aria-label", on ? "생성 중단" : "메시지 보내기");
  const glyph = sendBtn.querySelector("span");
  if (glyph) {
    glyph.textContent = on ? "■" : "↑";
  }
}

async function handleSubmit(event) {
  event.preventDefault();

  // 생성 중 클릭은 전송이 아니라 진행 중 응답 중단으로 동작한다.
  if (isGenerating) {
    currentAbortController?.abort();
    return;
  }

  const content = messageInput.value.trim();

  if (!content) {
    return;
  }

  const userMessage = addUserMessage(content);

  messageInput.value = "";
  autoResizeTextarea();
  isGenerating = true;
  setSendStopMode(true);
  setEngineStatus("working", "응답 스트리밍 중");

  const hideLoadingToast = showLoadingToast("흘러간 시간과, 머물다 간 감정들을 기록하고 있습니다...");
  const typing = addTypingAssistant(userMessage.id);
  let streamedContent = "";

  try {
    const reply = await requestAssistantReply(content, {
      clientMessageId: userMessage.id,
      onToken: (token) => {
        streamedContent += token;
        const typingIndex = messages.findIndex((message) => message.id === typing.id);
        if (typingIndex >= 0) {
          const nearBottom = isScrollNearBottom();
          messages[typingIndex] = {
            ...messages[typingIndex],
            typing: false,
            streaming: true,
            content: streamedContent
          };
          rerenderMessageRow(typing.id);
          if (nearBottom) {
            scrollToBottom();
          }
        }
      }
    });
    hideLoadingToast();
    replaceTypingWithAssistant(typing.id, reply, userMessage.id);
    await loadConversationList();
    setEngineStatus("ready", "엔진 준비됨");
  } catch (error) {
    hideLoadingToast();
    if (error?.name === "AbortError") {
      // 사용자가 중단함 — 진행 중이던 assistant 임시 메시지는 폐기하고 유저 입력만 남긴다.
      messages = messages.filter((message) => message.id !== typing.id);
      setEngineStatus("ready", "엔진 준비됨");
    } else {
      replaceTypingWithAssistant(
        typing.id,
        makeAssistantPayload({
          place: "오류 로그",
          analyze: "백엔드 요청 또는 응답 처리 중 오류가 발생했다.",
          body: `응답을 가져오지 못했습니다.\n\n${error.message || "백엔드 연결 상태를 확인해주세요."}`
        }),
        userMessage.id
      );
      setEngineStatus("error", "연결 확인 필요");
      console.error(error);
    }
  } finally {
    currentAbortController = null;
    isGenerating = false;
    setSendStopMode(false);
    messageInput.focus();
    renderMessages();
  }
}

async function rerollAssistant(assistantId, actorModel = selectedActorModel) {
  if (isGenerating) return;

  const assistantIndex = messages.findIndex((message) => message.id === assistantId);
  const assistant = messages[assistantIndex];

  if (!assistant || assistant.role !== "assistant") {
    return;
  }

  const parentUser = messages.find((message) => message.id === assistant.parentUserId);
  const rerollModel = normalizeActorModel(actorModel || assistant.actorModel || selectedActorModel);

  if (!parentUser) {
    showToast("이 assistant 메시지와 연결된 유저 메시지를 찾을 수 없습니다.");
    return;
  }

  isGenerating = true;
  editingMessageId = null;
  editDraft = "";

  const hideLoadingToast = showLoadingToast("같은 입력을 바탕으로 응답을 다시 구성하고 있습니다...");

  messages[assistantIndex] = {
    ...assistant,
    typing: true,
    content: "",
    actorModel: rerollModel,
    edited: false
  };
  renderMessages();

  try {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/messages/${encodeURIComponent(assistant.id)}/reroll`,
      {
        method: "POST",
        body: JSON.stringify({ actor_model: rerollModel })
      }
    );
    const reply = normalizeServerMessage(result.message);
    const rerolledVariants = reply.variants || assistant.variants || [];
    messages[assistantIndex] = {
      ...assistant,
      id: reply.id,
      content: reply.content,
      createdAt: reply.createdAt,
      parentUserId: reply.parentUserId,
      actorModel: reply.actorModel || rerollModel,
      variants: rerolledVariants,
      activeVersionIdx: rerolledVariants.length,
      typing: false,
      edited: reply.edited
    };
    await loadConversationList();
  } catch (error) {
    // 서버가 reroll을 거부(예: 이미 커밋된 과거 응답)하거나 실패하면 낙관적 변경을 되돌려
    // 원본 메시지를 그대로 복원한다 — 오류 안내는 토스트로 보여 로컬 상태 손상을 막는다.
    messages[assistantIndex] = assistant;
    console.error(error);
    showToast("응답을 다시 생성하지 못했습니다.");
  } finally {
    hideLoadingToast();
    isGenerating = false;
    renderMessages();
    updateConversationInfo();
    scrollToBottom();
  }
}

function startEdit(messageId) {
  if (isGenerating) return;

  const message = messages.find((item) => item.id === messageId);

  if (!message || message.typing) {
    return;
  }

  editingMessageId = messageId;
  editDraft = message.content;
  renderMessages();
  updateConversationInfo();
}

function cancelEdit() {
  editingMessageId = null;
  editDraft = "";
  renderMessages();
}

async function saveEdit(messageId) {
  const messageIndex = messages.findIndex((item) => item.id === messageId);
  const message = messages[messageIndex];

  if (!message) {
    return;
  }

  const nextContent = editDraft.trim();

  if (!nextContent) {
    showToast("빈 메시지로 저장할 수 없습니다.");
    return;
  }

  editingMessageId = null;
  editDraft = "";

  if (message.role === "assistant") {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/messages/${encodeURIComponent(message.id)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ content: nextContent, actor_model: selectedActorModel })
      }
    );
    const updatedMessage = normalizeServerMessage(result.message);
    messages[messageIndex] = updatedMessage;
    await loadConversationList();
    renderMessages();
    updateConversationInfo();
    return;
  }

  messages[messageIndex] = {
    ...message,
    content: nextContent,
    edited: true
  };
  renderMessages();

  const pairedAssistantIndex = messages.findIndex((item) => item.role === "assistant" && item.parentUserId === message.id);

  if (pairedAssistantIndex < 0) {
    return;
  }

  isGenerating = true;
  const hideLoadingToast = showLoadingToast("수정된 입력을 기준으로 응답을 다시 생성하고 있습니다...");

  const oldAssistant = messages[pairedAssistantIndex];
  messages[pairedAssistantIndex] = {
    ...oldAssistant,
    typing: true,
    content: "",
    edited: false
  };
  renderMessages();

  try {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/messages/${encodeURIComponent(message.id)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ content: nextContent, actor_model: selectedActorModel })
      }
    );
    const reply = normalizeServerMessage(result.message);
    messages[pairedAssistantIndex] = {
      ...oldAssistant,
      id: reply.id,
      content: reply.content,
      createdAt: reply.createdAt,
      parentUserId: reply.parentUserId,
      actorModel: reply.actorModel || selectedActorModel,
      variants: reply.variants || oldAssistant.variants || [],
      typing: false,
      edited: reply.edited
    };
    await loadConversationList();
  } catch (error) {
    messages[pairedAssistantIndex] = {
      ...oldAssistant,
      content: makeAssistantPayload({
        place: "오류 로그",
        analyze: "유저 메시지 수정 후 재생성 중 오류가 발생했다.",
        body: "수정된 입력으로 응답을 다시 생성하지 못했습니다."
      }),
      createdAt: getCurrentTime(),
      actorModel: selectedActorModel,
      variants: oldAssistant.variants || [],
      typing: false,
      edited: false
    };
    console.error(error);
  } finally {
    hideLoadingToast();
    isGenerating = false;
    renderMessages();
    updateConversationInfo();
    scrollToBottom();
  }
}

async function deleteMessage(messageId) {
  if (isGenerating) return;

  const message = messages.find((item) => item.id === messageId);

  if (!message) {
    return;
  }

  const label = message.role === "user"
    ? "이 유저 메시지와 연결된 assistant 응답을 함께 삭제할까요?"
    : "이 assistant 메시지를 삭제할까요?";

  if (!window.confirm(label)) {
    return;
  }

  try {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/messages/${encodeURIComponent(message.id)}`,
      { method: "DELETE" }
    );
    messages = (result.messages || []).map(normalizeServerMessage);
    await loadConversationList();
  } catch (error) {
    console.error(error);
    showToast("메시지를 삭제하지 못했습니다.");
    return;
  }

  if (editingMessageId === message.id) {
    editingMessageId = null;
    editDraft = "";
  }

  renderMessages();
}

function applyTheme(theme) {
  const isDark = theme === "dark";
  document.body.classList.toggle("dark", isDark);
  themeIcon.textContent = isDark ? "☀" : "☾";
  localStorage.setItem(STORAGE_KEY, theme);
}

function applySidebarCollapsed(collapsed) {
  isSidebarCollapsed = Boolean(collapsed);
  document.querySelector(".app-shell")?.classList.toggle("sidebar-collapsed", isSidebarCollapsed);
  localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, isSidebarCollapsed ? "true" : "false");
}

function toggleTheme() {
  const nextTheme = document.body.classList.contains("dark") ? "light" : "dark";
  applyTheme(nextTheme);
}

async function clearChat() {
  if (isGenerating) return;

  await resetChatForSelectedScenario();
}

function closeSidebarOnMobile() {
  sidebar.classList.remove("open");
}

function openSidebar() {
  applySidebarCollapsed(false);
  sidebar.classList.add("open");
}

function closeSidebar() {
  if (window.matchMedia("(max-width: 820px)").matches) {
    closeSidebarOnMobile();
    return;
  }
  applySidebarCollapsed(true);
}


// ── OOC 설정 모달 ──────────────────────────────────────────────

const oocModal = document.querySelector("#oocModal");
const oocConfigTextarea = document.querySelector("#oocConfigTextarea");
let oocModalDraft = "";

function openOocModal() {
  modalReturnFocus = document.activeElement;
  oocModalDraft = currentOocConfig;
  oocConfigTextarea.value = currentOocConfig;
  oocModal.hidden = false;
  oocConfigTextarea.focus();
}

function closeOocModal() {
  oocModal.hidden = true;
}

async function saveOocConfig() {
  const value = oocConfigTextarea.value;
  currentOocConfig = value;
  closeOocModal();

  if (!currentThreadId) {
    return;
  }

  try {
    await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/ooc-config`,
      { method: "PATCH", body: JSON.stringify({ ooc_config: value }) }
    );
  } catch (error) {
    console.error("OOC config save failed:", error);
    showToast("OOC 설정을 저장하지 못했습니다.");
  }
}

document.querySelector("#oocModalClose")?.addEventListener("click", () => {
  oocConfigTextarea.value = oocModalDraft;
  closeOocModal();
});

document.querySelector("#oocModalCancel")?.addEventListener("click", () => {
  oocConfigTextarea.value = oocModalDraft;
  closeOocModal();
});

document.querySelector("#oocModalSave")?.addEventListener("click", () => void saveOocConfig());

oocConfigBtn?.addEventListener("click", openOocModal);

// ── 임신 도구 모달 (강제 임신 / 질내사정 시뮬레이션) ──────────────
const forcePregnancyBtn = document.querySelector("#forcePregnancyBtn");
const simPregnancyBtn = document.querySelector("#simPregnancyBtn");
const forcePregnancyModal = document.querySelector("#forcePregnancyModal");
const simPregnancyModal = document.querySelector("#simPregnancyModal");

function fillCharacterSelect(select, { withNone = false, placeholder = "캐릭터 선택" } = {}) {
  if (!select) {
    return;
  }
  const characters = locationBoard?.characters || [];
  const options = [];
  if (withNone) {
    options.push(`<option value="">(지정 안 함)</option>`);
  } else if (!characters.length) {
    options.push(`<option value="">${escapeHTML(placeholder)}</option>`);
  }
  for (const character of characters) {
    const label = character.name && character.name !== character.id
      ? `${character.name} (${character.id})`
      : character.id;
    options.push(`<option value="${escapeHTML(character.id)}">${escapeHTML(label)}</option>`);
  }
  select.innerHTML = options.join("");
}

async function ensureCharacterBoard() {
  const threadId = await ensureConversation();
  locationBoard = await apiFetchJSON(`/api/conversations/${encodeURIComponent(threadId)}/locations`);
  return threadId;
}

async function openPregnancyModal(modal, fillFn) {
  modalReturnFocus = document.activeElement;
  try {
    await ensureCharacterBoard();
  } catch (error) {
    console.error("character board load failed:", error);
    showToast("캐릭터 목록을 불러오지 못했습니다.");
    return;
  }
  if (!(locationBoard?.characters || []).length) {
    showToast("이 대화에는 임신시킬 캐릭터가 없습니다.");
    return;
  }
  fillFn();
  modal.hidden = false;
}

function openForcePregnancyModal() {
  return openPregnancyModal(forcePregnancyModal, () => {
    fillCharacterSelect(document.querySelector("#forcePregMother"));
    fillCharacterSelect(document.querySelector("#forcePregFather"), { withNone: true });
  });
}

function openSimPregnancyModal() {
  return openPregnancyModal(simPregnancyModal, () => {
    fillCharacterSelect(document.querySelector("#simPregMother"));
    fillCharacterSelect(document.querySelector("#simPregFather"), { withNone: true });
  });
}

async function runPregnancyTool(path, payload, statusText) {
  if (isGenerating) {
    return;
  }
  isGenerating = true;
  sendBtn.disabled = true;

  const hideLoadingToast = showLoadingToast(statusText);
  const typing = addTypingAssistant(null);

  try {
    const threadId = await ensureConversation();
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(threadId)}${path}`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    replaceTypingWithAssistant(typing.id, normalizeServerMessage(result.message), null);
    await loadConversationList();
    updateConversationInfo(result.preview || result.message?.content || null);
  } catch (error) {
    console.error("pregnancy tool failed:", error);
    replaceTypingWithAssistant(
      typing.id,
      makeAssistantPayload({
        place: "임신 도구 오류",
        analyze: "임신 도구 실행 중 오류가 발생했다.",
        body: "임신 처리에 실패했습니다. 캐릭터 선택을 확인해 주세요."
      }),
      null
    );
  } finally {
    hideLoadingToast();
    isGenerating = false;
    sendBtn.disabled = false;
    renderMessages();
    scrollToBottom();
  }
}

async function runForcePregnancy() {
  const motherId = document.querySelector("#forcePregMother")?.value || "";
  const fatherId = document.querySelector("#forcePregFather")?.value || "";
  if (!motherId) {
    showToast("임신할 캐릭터(엄마)를 선택하세요.");
    return;
  }
  forcePregnancyModal.hidden = true;
  await runPregnancyTool(
    "/pregnancy/force",
    { mother_id: motherId, father_id: fatherId || null },
    "강제 임신을 적용하는 중입니다..."
  );
}

async function runSimPregnancy() {
  const motherId = document.querySelector("#simPregMother")?.value || "";
  const fatherId = document.querySelector("#simPregFather")?.value || "";
  const shots = Math.max(1, parseInt(document.querySelector("#simPregShots")?.value || "1", 10) || 1);
  if (!motherId) {
    showToast("임신할 캐릭터(엄마)를 선택하세요.");
    return;
  }
  simPregnancyModal.hidden = true;
  await runPregnancyTool(
    "/pregnancy/simulate",
    { mother_id: motherId, father_id: fatherId || null, shots },
    "질내사정 임신 확률을 시뮬레이션하는 중입니다..."
  );
}

forcePregnancyBtn?.addEventListener("click", () => void openForcePregnancyModal());
simPregnancyBtn?.addEventListener("click", () => void openSimPregnancyModal());
document.querySelector("#forcePregnancyModalClose")?.addEventListener("click", () => { forcePregnancyModal.hidden = true; });
document.querySelector("#forcePregnancyCancel")?.addEventListener("click", () => { forcePregnancyModal.hidden = true; });
document.querySelector("#forcePregnancyRun")?.addEventListener("click", () => void runForcePregnancy());
document.querySelector("#simPregnancyModalClose")?.addEventListener("click", () => { simPregnancyModal.hidden = true; });
document.querySelector("#simPregnancyCancel")?.addEventListener("click", () => { simPregnancyModal.hidden = true; });
document.querySelector("#simPregnancyRun")?.addEventListener("click", () => void runSimPregnancy());
forcePregnancyModal?.addEventListener("click", (event) => {
  if (event.target === forcePregnancyModal) {
    forcePregnancyModal.hidden = true;
  }
});
simPregnancyModal?.addEventListener("click", (event) => {
  if (event.target === simPregnancyModal) {
    simPregnancyModal.hidden = true;
  }
});

// ── 앱 설정 모달 (전역, 채팅방 무관) ───────────────────────────
const settingsBtn = document.querySelector("#settingsBtn");
const settingsModal = document.querySelector("#settingsModal");
const outputRepairToggle = document.querySelector("#outputRepairToggle");
const fontSelect = document.querySelector("#fontSelect");

// 본문 글꼴(고딕/명조)은 로컬 전용 표시 설정 — body.serif 클래스로 본문·씬 헤더만 교체한다.
function applyFontChoice(font, persist = true) {
  const value = font === "serif" ? "serif" : "gothic";
  document.body.classList.toggle("serif", value === "serif");
  if (persist) {
    localStorage.setItem(FONT_STORAGE_KEY, value);
  }
  if (fontSelect) {
    fontSelect.value = value;
  }
}

fontSelect?.addEventListener("change", () => applyFontChoice(fontSelect.value));

async function openSettingsModal() {
  modalReturnFocus = document.activeElement;
  // 항상 서버의 최신값을 반영해 다른 세션/탭 변경과 동기화한다.
  try {
    const settings = await apiFetchJSON("/api/settings");
    outputRepairToggle.checked = Boolean(settings.output_repair_enabled);
  } catch (error) {
    console.error("Settings load failed:", error);
    showToast("설정을 불러오지 못했습니다.");
  }
  settingsModal.hidden = false;
}

function closeSettingsModal() {
  settingsModal.hidden = true;
}

async function saveOutputRepairSetting(enabled) {
  try {
    await apiFetchJSON("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ output_repair_enabled: enabled }),
    });
  } catch (error) {
    console.error("Settings save failed:", error);
    showToast("설정을 저장하지 못했습니다.");
    outputRepairToggle.checked = !enabled; // 실패 시 토글 원복
  }
}

outputRepairToggle?.addEventListener("change", () =>
  void saveOutputRepairSetting(outputRepairToggle.checked)
);
document.querySelector("#settingsModalClose")?.addEventListener("click", closeSettingsModal);
settingsBtn?.addEventListener("click", () => void openSettingsModal());

// ── 유저노트 모달 ──────────────────────────────────────────────

const usernotesModal = document.querySelector("#usernotesModal");
const usernoteList = document.querySelector("#usernoteList");
const usernoteListView = document.querySelector("#usernoteListView");
const usernoteDetailView = document.querySelector("#usernoteDetailView");
const usernotesModalTitle = document.querySelector("#usernotesModalTitle");
const usernoteNameInput = document.querySelector("#usernoteNameInput");
const usernoteContentTextarea = document.querySelector("#usernoteContentTextarea");
const usernoteToggleBtn = document.querySelector("#usernoteToggleBtn");
const usernoteDeleteBtn = document.querySelector("#usernoteDeleteBtn");

function openUsernotesModal() {
  modalReturnFocus = document.activeElement;
  showUsernoteListView();
  usernotesModal.hidden = false;
}

function closeUsernotesModal() {
  usernotesModal.hidden = true;
}

function showUsernoteListView() {
  usernotesModalTitle.textContent = "유저노트";
  usernoteListView.hidden = false;
  usernoteDetailView.hidden = true;
  document.querySelector("#usernoteNewBtn").hidden = false;
  renderUsernoteList();
}

function showUsernoteDetailView(note) {
  usernoteDetailNoteId = note ? note.id : null;
  usernoteDetailEnabled = note ? Boolean(note.enabled) : true;

  usernotesModalTitle.textContent = note ? "노트 편집" : "새 유저노트";
  usernoteNameInput.value = note ? note.name : "";
  usernoteContentTextarea.value = note ? note.content : "";
  usernoteToggleBtn.setAttribute("aria-checked", String(usernoteDetailEnabled));
  usernoteDeleteBtn.hidden = !note;
  document.querySelector("#usernoteNewBtn").hidden = true;

  usernoteListView.hidden = true;
  usernoteDetailView.hidden = false;
  usernoteNameInput.focus();
}

function renderUsernoteList() {
  if (!usernoteList) return;
  if (!currentUsernotes.length) {
    usernoteList.innerHTML = `<div class="usernote-empty">유저노트가 없습니다.<br>오른쪽 상단 + 버튼으로 추가하세요.</div>`;
    return;
  }
  usernoteList.innerHTML = "";
  currentUsernotes.forEach((note) => {
    const card = document.createElement("button");
    card.className = `usernote-card${note.enabled ? "" : " disabled"}`;
    card.type = "button";

    const toggle = document.createElement("button");
    toggle.className = "toggle-btn";
    toggle.type = "button";
    toggle.setAttribute("role", "switch");
    toggle.setAttribute("aria-checked", String(note.enabled));
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      void patchUsernote(note.id, { enabled: !note.enabled });
    });

    const body = document.createElement("div");
    body.className = "usernote-card-body";

    const title = document.createElement("div");
    title.className = "usernote-card-title";
    title.textContent = note.name || "(제목 없음)";

    const preview = document.createElement("div");
    preview.className = "usernote-card-preview";
    preview.textContent = note.content;

    body.append(title, preview);
    card.append(body, toggle);
    card.addEventListener("click", () => showUsernoteDetailView(note));
    usernoteList.appendChild(card);
  });
}

async function saveUsernoteDetail() {
  const name = usernoteNameInput.value.trim();
  const content = usernoteContentTextarea.value;

  if (!name) {
    showToast("노트 제목을 입력해주세요.");
    usernoteNameInput.focus();
    return;
  }

  if (!currentThreadId) {
    showToast("대화를 먼저 시작해야 유저노트를 저장할 수 있습니다.");
    return;
  }

  try {
    if (usernoteDetailNoteId) {
      await patchUsernote(usernoteDetailNoteId, { name, content, enabled: usernoteDetailEnabled });
    } else {
      const result = await apiFetchJSON(
        `/api/conversations/${encodeURIComponent(currentThreadId)}/usernotes`,
        { method: "POST", body: JSON.stringify({ name, content }) }
      );
      currentUsernotes = result.usernotes || [];
    }
    showUsernoteListView();
  } catch (error) {
    console.error("usernote save failed:", error);
    showToast("유저노트를 저장하지 못했습니다.");
  }
}

async function patchUsernote(noteId, fields) {
  if (!currentThreadId) return;
  try {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/usernotes/${encodeURIComponent(noteId)}`,
      { method: "PATCH", body: JSON.stringify(fields) }
    );
    currentUsernotes = result.usernotes || [];
    renderUsernoteList();
  } catch (error) {
    console.error("usernote patch failed:", error);
    showToast("유저노트를 수정하지 못했습니다.");
  }
}

async function deleteUsernote() {
  if (!usernoteDetailNoteId || !currentThreadId) return;
  try {
    const result = await apiFetchJSON(
      `/api/conversations/${encodeURIComponent(currentThreadId)}/usernotes/${encodeURIComponent(usernoteDetailNoteId)}`,
      { method: "DELETE" }
    );
    currentUsernotes = result.usernotes || [];
    showUsernoteListView();
  } catch (error) {
    console.error("usernote delete failed:", error);
    showToast("유저노트를 삭제하지 못했습니다.");
  }
}

usernotesBtn?.addEventListener("click", openUsernotesModal);

document.querySelector("#usernotesModalClose")?.addEventListener("click", closeUsernotesModal);
document.querySelector("#usernoteNewBtn")?.addEventListener("click", () => showUsernoteDetailView(null));
document.querySelector("#usernoteDetailCancel")?.addEventListener("click", showUsernoteListView);
document.querySelector("#usernoteDetailSave")?.addEventListener("click", () => void saveUsernoteDetail());
document.querySelector("#usernoteDeleteBtn")?.addEventListener("click", () => void deleteUsernote());

usernoteToggleBtn?.addEventListener("click", () => {
  usernoteDetailEnabled = !usernoteDetailEnabled;
  usernoteToggleBtn.setAttribute("aria-checked", String(usernoteDetailEnabled));
});

// ── 이벤트 핸들러 ──────────────────────────────────────────────

chatForm.addEventListener("submit", handleSubmit);

messageInput.addEventListener("input", autoResizeTextarea);

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

themeToggleBtn.addEventListener("click", toggleTheme);

openSidebarBtn?.addEventListener("click", openSidebar);

closeSidebarBtn?.addEventListener("click", closeSidebar);

newChatBtn.addEventListener("click", () => {
  void clearChat();
  closeSidebarOnMobile();
});

schemaPanelBtn?.addEventListener("click", () => {
  if (activeInspector === "schema") {
    closeInspector();
    return;
  }
  void openSchemaInspector();
});

locationPanelBtn?.addEventListener("click", () => {
  if (activeInspector === "locations") {
    closeInspector();
    return;
  }
  void openLocationInspector();
});

closeInspectorBtn?.addEventListener("click", closeInspector);

inspectorActionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.inspectorAction;
    if (action === "schema") {
      void openSchemaInspector();
    } else if (action === "locations") {
      void openLocationInspector();
    }
    closeSidebarOnMobile();
  });
});


worldTrigger?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleWorldDropdown();
});

scenarioTrigger?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleScenarioDropdown();
});

actorModelTrigger?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleActorModelDropdown();
});

document.addEventListener("click", (event) => {
  if (
    !profileSelector?.contains(event.target) &&
    !actorModelDropdown?.contains(event.target)
  ) {
    closeAllProfileDropdowns();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeAllProfileDropdowns();
    closeInspector();
    if (!oocModal?.hidden) {
      oocConfigTextarea.value = oocModalDraft;
      closeOocModal();
    }
    if (!usernotesModal?.hidden) {
      closeUsernotesModal();
    }
    if (!settingsModal?.hidden) {
      closeSettingsModal();
    }
    if (!forcePregnancyModal?.hidden) {
      forcePregnancyModal.hidden = true;
    }
    if (!simPregnancyModal?.hidden) {
      simPregnancyModal.hidden = true;
    }
  }
});

settingsModal?.addEventListener("click", (event) => {
  if (event.target === settingsModal) {
    closeSettingsModal();
  }
});

// OOC 설정 모달: 바깥 클릭 시 취소(드래프트 복원)
oocModal?.addEventListener("click", (event) => {
  if (event.target === oocModal) {
    oocConfigTextarea.value = oocModalDraft;
    closeOocModal();
  }
});

// 유저노트 모달: 바깥 클릭 시 닫기
usernotesModal?.addEventListener("click", (event) => {
  if (event.target === usernotesModal) {
    closeUsernotesModal();
  }
});

// ── 모달 접근성 (포커스 트랩 + 포커스 복귀) ───────────────────────
// 모달을 연 직후의 트리거 요소를 기억해 두고, 모달이 모두 닫히면 그 요소로 포커스를 되돌린다.
let modalReturnFocus = null;

function getOpenModal() {
  return document.querySelector(".modal-overlay:not([hidden])");
}

function getFocusableElements(container) {
  const selector = [
    "a[href]",
    "button:not([disabled])",
    "textarea:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    '[tabindex]:not([tabindex="-1"])'
  ].join(",");
  // offsetParent가 null이면 화면에 없는 것(숨겨진 하위 뷰 등)이므로 트랩 대상에서 제외한다.
  return Array.from(container.querySelectorAll(selector)).filter((el) => el.offsetParent !== null);
}

function setupModalA11y() {
  // hidden 속성이 모두 켜지면(모든 모달 닫힘) 트리거로 포커스 복귀.
  const observer = new MutationObserver(() => {
    if (!getOpenModal() && modalReturnFocus) {
      modalReturnFocus.focus?.();
      modalReturnFocus = null;
    }
  });
  document.querySelectorAll(".modal-overlay").forEach((modal) => {
    observer.observe(modal, { attributes: true, attributeFilter: ["hidden"] });
  });

  // Tab을 현재 열린 모달 안에 가둔다.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") {
      return;
    }
    const modal = getOpenModal();
    if (!modal) {
      return;
    }
    const focusables = getFocusableElements(modal);
    if (!focusables.length) {
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

async function initializeApp() {
  const savedTheme = localStorage.getItem(STORAGE_KEY);
  applyTheme(savedTheme || "light");
  applySelectedActorModel(localStorage.getItem(ACTOR_MODEL_STORAGE_KEY), false);
  applySidebarCollapsed(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true");
  applyFontChoice(localStorage.getItem(FONT_STORAGE_KEY) || "gothic", false);
  autoResizeTextarea();
  setupModalA11y();
  setEngineStatus("connecting", "엔진 연결 중");

  try {
    await loadWorldProfiles();
    await loadConversationList();
    renderProfileDropdown();
    await showStartMessage();
    setEngineStatus("ready", "엔진 준비됨");
  } catch (error) {
    console.error(error);
    renderProfileDropdown();
    await showStartMessage();
    setEngineStatus("error", "연결 확인 필요");
  }
}

initializeApp();
