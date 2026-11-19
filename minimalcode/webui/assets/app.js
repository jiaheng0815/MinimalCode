"use strict";

/* MinimalCode 的网页界面，结构对齐 deepseek-ai/chat-demo。

   页面只是同一个 agent 的另一个前端：会话、工具、设置都由本地服务提供，
   这里只负责渲染和把事件转成 DOM。 */

const PROTOCOL_LABELS = {
  openai_chat: "OpenAI Chat Completions",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
  deepseek_web: "DeepSeek Web",
};

const ICON_THINK =
  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8a3.7 3.7 0 0 0-3.7 3.7c0 1.2.5 2 1.2 2.6.5.5.7.8.7 1.4v.6h3.6v-.6c0-.6.2-.9.7-1.4.7-.6 1.2-1.4 1.2-2.6A3.7 3.7 0 0 0 8 1.8Z"/><path d="M6.2 12.2h3.6M6.8 14h2.4"/></svg>';

const ICON_CHEVRON =
  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.4 6.3 8 9.9l3.6-3.6"/></svg>';
const ICON_FOLDER =
  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.8 4.4c0-.6.5-1.1 1.1-1.1h3l1.2 1.4h5c.6 0 1.1.5 1.1 1.1v5.8c0 .6-.5 1.1-1.1 1.1H2.9c-.6 0-1.1-.5-1.1-1.1Z"/></svg>';
const ICON_TRASH =
  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 4.6h9.6M6.4 4.6V3.4c0-.4.3-.7.7-.7h1.8c.4 0 .7.3.7.7v1.2M5 4.6l.5 8c0 .4.3.7.7.7h3.6c.4 0 .7-.3.7-.7l.5-8"/></svg>';
const ICON_PENCIL =
  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10.6 2.9 2.5 2.5-7.2 7.2-3 .5.5-3Z"/></svg>';

const $ = (id) => document.getElementById(id);
const el = (tag, className) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
};

const app = {
  state: null,
  sessions: [],
  folders: [],
  messages: [],
  streaming: false,
  // 读取偏好时不能抛异常：隐私模式下 localStorage 不可用，而这里一旦抛出，
  // boot() 之前脚本就停了，整页只剩空壳。
  theme: storageGet("minimalcode-theme") || "system",
  tab: "model",
  browsing: null,
  turn: null,
  // 折叠状态只存在于这台浏览器，文件夹本身由服务端保存。
  collapsed: readCollapsed(),
  // 思考块默认展开：流式时整段推理本来就是看得见的，刷新重建后也保持一致；
  // 手动折叠过一次，后面的页面跟着折叠（同一台浏览器）。
  thinkingOpen: readThinkingOpen(),
  // 宽屏下边栏是否收起。收起后唯一的展开入口是 .main 里的 mobile-menu，
  // 因为 .sider 自己被 hidden 了。
  siderHidden: false,
  menu: null,
};

function readCollapsed() {
  try {
    const stored = JSON.parse(localStorage.getItem("minimalcode-folders-collapsed") || "[]");
    if (!Array.isArray(stored)) return new Set();
    return new Set(stored.filter((item) => typeof item === "string"));
  } catch {
    return new Set();
  }
}

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* 隐私模式下 localStorage 不可写：偏好只在本次页面里生效。 */
  }
}

function readThinkingOpen() {
  return storageGet("minimalcode-thinking-collapsed") !== "1";
}

function saveThinkingOpen(open) {
  storageSet("minimalcode-thinking-collapsed", open ? "0" : "1");
}

const dom = {};

/* ---------------------------------------------------------------- helpers */

function baseName(path) {
  const parts = String(path || "").split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(path || "");
}

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
}

function apiError(payload, response) {
  return (payload && payload.error) || `${response.status} ${response.statusText}`;
}

async function api(path, body) {
  const init =
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        };
  const response = await fetch(path, init);
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: text };
    }
  }
  if (!response.ok) throw new Error(apiError(payload, response));
  return payload;
}

function toast(message) {
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    dom.toast.hidden = true;
  }, 2400);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请长按或选中文本后复制");
  }
}

/* --------------------------------------------------------------- markdown */

function inline(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  out = out.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  out = out.replace(
    /\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>',
  );
  return out;
}

function codeBlock(lang, code) {
  const label = escapeHtml(lang || "text");
  return (
    '<div class="md-code-block">' +
    '<div class="md-code-block-banner">' +
    `<span>${label}</span>` +
    '<button type="button" class="md-code-block-copy">复制</button>' +
    "</div>" +
    `<pre><code>${escapeHtml(code)}</code></pre>` +
    "</div>"
  );
}

function parseList(lines, start) {
  const first = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[start]);
  const base = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let current = null;
  let index = start;
  while (index < lines.length) {
    const line = lines[index];
    const match = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (!match) {
      if (!line.trim()) {
        index += 1;
        break;
      }
      if (current && /^\s{2,}/.test(line)) {
        current.text.push(line.trim());
        index += 1;
        continue;
      }
      break;
    }
    const indent = match[1].length;
    if (indent < base) break;
    if (indent >= base + 2 && current) {
      const [nested, next] = parseList(lines, index);
      current.nested.push(nested);
      index = next;
      continue;
    }
    current = { text: [match[3]], nested: [] };
    items.push(current);
    index += 1;
  }
  const tag = ordered ? "ol" : "ul";
  const body = items
    .map((item) => `<li>${inline(item.text.join("\n"))}${item.nested.join("")}</li>`)
    .join("");
  return [`<${tag}>${body}</${tag}>`, index];
}

function parseTable(lines, start) {
  if (start + 1 >= lines.length) return ["", start];
  if (!/^\s*\|?[\s:|-]*-{2,}[\s:|-]*$/.test(lines[start + 1])) return ["", start];
  const cell = (row) =>
    row
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((part) => part.trim());
  const rows = [];
  let index = start;
  while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
    rows.push(lines[index]);
    index += 1;
  }
  if (rows.length < 2) return ["", start];
  const head = cell(rows[0]);
  const body = rows.slice(2).map(cell);
  const th = head.map((part) => `<th>${inline(part)}</th>`).join("");
  const tr = body
    .map((row) => `<tr>${row.map((part) => `<td>${inline(part)}</td>`).join("")}</tr>`)
    .join("");
  return [
    `<div class="table-wrapper"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`,
    index,
  ];
}

function markdownToHtml(source) {
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  let html = "";
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const fence = /^\s*```(\S*)\s*$/.exec(line);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      html += codeBlock(fence[1], body.join("\n"));
      continue;
    }
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      html += `<h${level}>${inline(heading[2])}</h${level}>`;
      index += 1;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      html += "<hr />";
      index += 1;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      html += `<blockquote>${markdownToHtml(quote.join("\n"))}</blockquote>`;
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const [block, next] = parseList(lines, index);
      html += block;
      index = next;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const [block, next] = parseTable(lines, index);
      if (block) {
        html += block;
        index = next;
        continue;
      }
    }
    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{1,6})\s|^\s*```|^>|^\s*([-*+]|\d+[.)])\s+/.test(lines[index]) &&
      !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    if (paragraph.length) {
      html += `<p>${inline(paragraph.join("\n"))}</p>`;
    } else {
      index += 1;
    }
  }
  return html;
}

function paintMarkdown(node, source) {
  node.innerHTML = markdownToHtml(source);
}

/* --------------------------------------------------------------- messages */

function scrollToBottom() {
  const list = dom.messageList;
  const distance = list.scrollHeight - list.scrollTop - list.clientHeight;
  if (distance < 200) list.scrollTop = list.scrollHeight;
}

function clearMessages() {
  dom.messages.replaceChildren();
}

function addUserMessage(text) {
  const article = el("article", "user-message");
  const bubble = el("div", "user-bubble");
  bubble.textContent = text;
  article.append(bubble);
  dom.messages.append(article);
  scrollToBottom();
}

function startRound(turn) {
  // 一次模型请求就是一轮：本轮推理、本轮正文、本轮工具活动放在同一个块里，
  // 于是页面顺序始终是「思考 → 正文 → 工具」，而不会把多轮内容堆成一坨。
  const current = turn.current;
  if (
    current &&
    !current.thinking &&
    !current.text &&
    !current.tools.children.length
  ) {
    // 空的当前段（页面先建好等待的那一段）直接复用，避免空块占位。
    return current;
  }
  const round = el("div", "assistant-round");
  const reply = el("div", "reply-area");
  reply.hidden = true;
  const replyTitle = el("div", "reply-title");
  // 标题是一个可点、可聚焦的开关：展开/折叠这段推理，键盘同样能操作。
  replyTitle.setAttribute("role", "button");
  replyTitle.setAttribute("tabindex", "0");
  replyTitle.setAttribute("aria-expanded", "true");
  replyTitle.innerHTML = `<span class="reply-title-icon">${ICON_THINK}</span><span class="reply-title-text shimmer">正在思考</span><span class="reply-title-chevron">${ICON_CHEVRON}</span>`;
  const thinkBody = el("div", "ds-think-content");
  const thinkMarkdown = el("div", "ds-markdown");
  thinkBody.append(thinkMarkdown);
  reply.append(replyTitle, thinkBody);

  const content = el("div", "ds-markdown");
  const tools = el("div", "tool-rows");
  tools.hidden = true;
  round.append(reply, content, tools);
  turn.rounds.append(round);
  turn.current = {
    element: round,
    reply,
    replyTitle,
    thinkBody,
    thinkMarkdown,
    content,
    tools,
    thinking: "",
    text: "",
    thinkDone: false,
    started: Date.now(),
  };
  const created = turn.current;
  replyTitle.addEventListener("click", () => toggleThinking(created));
  replyTitle.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    toggleThinking(created);
  });
  return created;
}

function setThinkingOpen(round, open) {
  round.thinkBody.hidden = !open;
  round.replyTitle.classList.toggle("collapsed", !open);
  round.replyTitle.setAttribute("aria-expanded", open ? "true" : "false");
  app.thinkingOpen = Boolean(open);
  saveThinkingOpen(app.thinkingOpen);
}

function toggleThinking(round) {
  // 没有推理内容的那一段不展开：点了也只是一块空白。
  if (!round || round.reply.hidden) return;
  setThinkingOpen(round, round.thinkBody.hidden);
}

function createAssistantMessage() {
  const article = el("article", "assistant-message");
  const rounds = el("div", "assistant-rounds");

  const waiting = el("div", "waiting");
  waiting.innerHTML =
    '<span class="waiting-dot"></span><span class="waiting-dot"></span><span class="waiting-dot"></span>';

  const notice = el("div", "notice");
  notice.hidden = true;

  const footer = el("div", "assistant-footer");
  const copyButton = el("button", "footer-button");
  copyButton.type = "button";
  copyButton.textContent = "复制";
  const usage = el("div", "footer-usage");
  footer.append(copyButton, usage);

  article.append(rounds, waiting, notice, footer);
  dom.messages.append(article);
  scrollToBottom();

  const turn = {
    article,
    rounds,
    waiting,
    notice,
    copyButton,
    usage,
    current: null,
    text: "",
    frame: 0,
    pending: new Map(),
  };
  startRound(turn);
  copyButton.addEventListener("click", () => copyText(turn.text || ""));
  return turn;
}

function schedulePaint(turn, node, read) {
  // 同一个节点只保留最后一次读取，帧回调里取当时的文本，所以思考与正文
  // 交替到达时都不会被对方的节流吞掉。
  turn.pending.set(node, read);
  if (turn.frame) return;
  turn.frame = requestAnimationFrame(() => {
    turn.frame = 0;
    for (const [target, source] of turn.pending) paintMarkdown(target, source());
    turn.pending.clear();
    scrollToBottom();
  });
}

function finishThinking(turn, round) {
  const target = round || turn.current;
  if (!target || target.thinkDone) return;
  target.thinkDone = true;
  // No reasoning block was opened, so there is no duration to report.
  if (target.reply.hidden) return;
  const seconds = Math.max(1, Math.round((Date.now() - target.started) / 1000));
  const label = target.replyTitle.querySelector(".reply-title-text");
  if (!label) return;
  label.classList.remove("shimmer");
  label.textContent = `已思考（用时 ${seconds} 秒）`;
  // 折叠偏好来自这台浏览器：默认保持展开，和刷新后重建的观感一致；
  // 偏好折叠时正文一开始就把推理收起来，而不是等下一次刷新。
  if (!app.thinkingOpen) setThinkingOpen(target, false);
}

function restoreThinking(round, entry) {
  // 刷新后 /api/history 带回这一段推理。展开状态沿用这台浏览器的偏好
  // （默认展开，和流式时一样看得见），标题始终可以点开/收起。
  round.reply.hidden = false;
  round.thinkDone = true;
  round.thinking = entry.text;
  paintMarkdown(round.thinkMarkdown, entry.text);
  setThinkingOpen(round, app.thinkingOpen);
  const label = round.replyTitle.querySelector(".reply-title-text");
  if (!label) return;
  label.classList.remove("shimmer");
  label.textContent = entry.seconds ? `已思考（用时 ${entry.seconds} 秒）` : "已思考";
}

function applyEvent(event, payload, turn) {
  if (event === "round") {
    startRound(turn);
  } else if (event === "delta") {
    const text = payload.text || "";
    if (!text) return;
    turn.waiting.remove();
    const round = turn.current || startRound(turn);
    finishThinking(turn, round);
    round.text += text;
    turn.text += text;
    schedulePaint(turn, round.content, () => round.text);
  } else if (event === "thinking") {
    const text = payload.text || "";
    if (!text) return;
    let round = turn.current || startRound(turn);
    // 正文或工具已经开始，说明这一段结束了：迟到的推理单独开一段，
    // 既不会挤进正文块上方，也不会把正文写进推理块里。
    if (round.text || round.tools.children.length) round = startRound(turn);
    round.reply.hidden = false;
    round.thinking += text;
    schedulePaint(turn, round.thinkMarkdown, () => round.thinking);
  } else if (event === "activity" || event === "notice") {
    const round = turn.current || startRound(turn);
    round.tools.hidden = false;
    turn.waiting.remove();
    const row = el("div", "tool-row");
    const dot = el("span", "tool-dot");
    if (event === "notice") dot.classList.add("inactive");
    const text = el("div", "tool-text");
    text.textContent = payload.text || "";
    row.append(dot, text);
    round.tools.append(row);
    scrollToBottom();
  } else if (event === "start") {
    setProviderLabel(payload.provider, payload.model);
  } else if (event === "done") {
    turn.waiting.remove();
    finishThinking(turn);
    if (!turn.text && payload.text) {
      const round = turn.current || startRound(turn);
      round.text = payload.text;
      turn.text = payload.text;
      paintMarkdown(round.content, turn.text);
    }
    const usage = payload.usage || {};
    const parts = [];
    if (usage.prompt_tokens) parts.push(`输入 ${usage.prompt_tokens}`);
    if (usage.completion_tokens) parts.push(`输出 ${usage.completion_tokens}`);
    if (parts.length) {
      turn.usage.textContent = `${parts.join(" · ")} tokens${payload.usage_estimated ? "（估算）" : ""}`;
    }
    if (payload.completed === false) {
      turn.notice.hidden = false;
      turn.notice.textContent = "本次回答没有正常结束";
    }
    if (payload.session) app.state.session = payload.session;
    scrollToBottom();
  } else if (event === "error") {
    turn.waiting.remove();
    finishThinking(turn);
    turn.notice.hidden = false;
    turn.notice.textContent = payload.message || "未知错误";
  }
}

function parseFrame(frame) {
  let event = "message";
  const data = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  try {
    return { event, payload: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}

/* -------------------------------------------------------------- sider bar */

function renderSider() {
  closeMenu();
  dom.siderList.replaceChildren();
  const activeId = app.state && app.state.session ? app.state.session.id : null;
  // 第一层是文件夹，第二层是文件夹里的对话；没有归档的对话放进「未分类」。
  const filed = new Map(app.folders.map((folder) => [folder.id, []]));
  const loose = [];
  for (const session of app.sessions) {
    const bucket = filed.get(session.folder);
    if (bucket) bucket.push(session);
    else loose.push(session);
  }
  for (const folder of app.folders) {
    dom.siderList.append(folderSection(folder, filed.get(folder.id), activeId));
  }
  if (loose.length) {
    dom.siderList.append(folderSection({ id: "", name: "未分类" }, loose, activeId));
  }
  if (!app.folders.length && !loose.length) {
    const empty = el("div", "sider-empty");
    empty.textContent = "还没有对话记录";
    dom.siderList.append(empty);
  }
}

function folderSection(folder, sessions, activeId) {
  const items = sessions || [];
  const section = el("div", "sider-section");
  const collapsed = Boolean(folder.id) && app.collapsed.has(folder.id);
  const header = el("div", "folder-header");
  header.classList.toggle("collapsed", collapsed);
  const chevron = el("span", "folder-chevron");
  chevron.innerHTML = ICON_CHEVRON;
  const name = el("span", "folder-name");
  name.textContent = folder.name;
  name.title = folder.name;
  header.append(chevron, name);
  if (items.length) {
    const count = el("span", "folder-count");
    count.textContent = String(items.length);
    header.append(count);
  }
  if (folder.id) {
    header.append(folderActions(folder));
    header.addEventListener("click", () => toggleFolder(folder.id));
  }
  section.append(header);
  if (collapsed) return section;
  const children = el("div", "folder-children");
  if (!items.length) {
    const empty = el("div", "folder-empty");
    empty.textContent = "还没有对话";
    children.append(empty);
  }
  for (const session of items) children.append(sessionRow(session, activeId));
  section.append(children);
  return section;
}

function sessionRow(session, activeId) {
  const item = el("div", "session-item");
  if (session.id === activeId) item.classList.add("active");
  const title = el("div", "session-title");
  title.textContent = session.title || "新对话";
  title.title = session.title || "新对话";
  const actions = el("div", "session-actions");
  const move = actionButton("move", ICON_FOLDER, "移入文件夹");
  move.addEventListener("click", (event) => {
    event.stopPropagation();
    openFolderMenu(move, session);
  });
  const remove = actionButton("delete", ICON_TRASH, "删除对话");
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteSession(session);
  });
  actions.append(move, remove);
  item.append(title, actions);
  item.addEventListener("click", () => openSession(session.id));
  return item;
}

function actionButton(kind, icon, label) {
  const button = el("button", `session-action session-action-${kind}`);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = icon;
  return button;
}

function folderActions(folder) {
  const actions = el("div", "folder-actions");
  const rename = actionButton("rename", ICON_PENCIL, "重命名文件夹");
  rename.addEventListener("click", (event) => {
    event.stopPropagation();
    renameFolder(folder);
  });
  const remove = actionButton("delete", ICON_TRASH, "删除文件夹");
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteFolder(folder);
  });
  actions.append(rename, remove);
  return actions;
}

function toggleFolder(id) {
  if (app.collapsed.has(id)) app.collapsed.delete(id);
  else app.collapsed.add(id);
  storageSet("minimalcode-folders-collapsed", JSON.stringify([...app.collapsed]));
  renderSider();
}

/* ---------------------------------------------------------- folder actions */

function closeMenu() {
  if (!app.menu) return;
  app.menu.remove();
  app.menu = null;
}

function menuItem(label, onPick, selected) {
  const item = el("button", "session-menu-item");
  item.type = "button";
  item.textContent = label;
  if (selected) item.classList.add("selected");
  item.addEventListener("click", () => {
    closeMenu();
    onPick();
  });
  return item;
}

function openFolderMenu(anchor, session) {
  closeMenu();
  const menu = el("div", "session-menu");
  const title = el("div", "session-menu-title");
  title.textContent = "移入文件夹";
  menu.append(title);
  for (const folder of app.folders) {
    menu.append(menuItem(folder.name, () => moveSession(session, folder.id), session.folder === folder.id));
  }
  menu.append(menuItem("未分类", () => moveSession(session, null), !session.folder));
  menu.append(menuItem("新建文件夹…", () => createFolderAndMove(session)));
  menu.style.visibility = "hidden";
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - height - 8))}px`;
  menu.style.visibility = "";
  app.menu = menu;
}

function setProviderLabel(provider, model) {
  if (!app.state) return;
  app.state.provider = provider || app.state.provider;
  app.state.model = model || app.state.model;
}

function updateSendState() {
  const busy = app.streaming || Boolean(app.state && app.state.busy);
  dom.send.disabled = busy;
  dom.send.classList.toggle("disabled", busy);
  dom.prompt.placeholder = busy ? "正在生成回答..." : "请输入消息";
}

function updateWorkspaceLabel() {
  const workspace = app.state ? app.state.workspace : "";
  dom.workspaceLabel.textContent = baseName(workspace) || "工作目录";
  dom.workspaceToggle.title = workspace || "选择本地文件夹";
}

function isNarrow() {
  return window.matchMedia("(max-width: 767px)").matches;
}

function openDrawer() {
  dom.sider.classList.add("sider-open");
  dom.siderMask.hidden = false;
}

function closeDrawer() {
  dom.sider.classList.remove("sider-open");
  dom.siderMask.hidden = true;
}

function setSiderHidden(hidden) {
  // 收起只改这一个状态位：.sider[hidden] 与 .main 里的展开按钮都看它。
  app.siderHidden = Boolean(hidden);
  dom.sider.hidden = app.siderHidden;
  const label = app.siderHidden ? "打开边栏" : "收起边栏";
  dom.siderToggle.title = label;
  dom.siderToggle.setAttribute("aria-label", label);
}

function showWelcome() {
  dom.view.hidden = true;
  dom.welcome.hidden = false;
  dom.composer.classList.add("home");
  dom.composer.classList.remove("session");
  dom.welcomeInput.append(dom.composer);
  autoGrow();
  updateSendState();
}

function showSessionView() {
  dom.welcome.hidden = true;
  dom.view.hidden = false;
  dom.composer.classList.remove("home");
  dom.composer.classList.add("session");
  dom.sessionInput.append(dom.composer);
  autoGrow();
  updateSendState();
}

function renderState(state) {
  app.state = state;
  document.title = state.session && state.session.title ? `${state.session.title} · MinimalCode` : "MinimalCode";
  dom.viewTitle.textContent = (state.session && state.session.title) || "新对话";
  dom.thinkingToggle.classList.toggle("selected", Boolean(state.thinking));
  updateWorkspaceLabel();
  updateSendState();
}

function renderConversation() {
  clearMessages();
  for (const message of app.messages) {
    if (message.role === "user") {
      addUserMessage(message.content);
      continue;
    }
    const turn = createAssistantMessage();
    turn.waiting.remove();
    const reasoning = (Array.isArray(message.thinking) ? message.thinking : []).filter(
      (entry) => entry && typeof entry.text === "string" && entry.text
    );
    reasoning.forEach((entry, index) => {
      restoreThinking(turn.current, entry);
      // 除最后一段外，每段推理自成一块；最后一段与正文同块，和流式时一致。
      if (index < reasoning.length - 1) startRound(turn);
    });
    turn.text = message.content;
    turn.current.text = message.content;
    paintMarkdown(turn.current.content, message.content);
  }
  if (app.messages.length) {
    showSessionView();
    dom.messageList.scrollTop = dom.messageList.scrollHeight;
  } else if (app.state && app.state.session) {
    showSessionView();
  } else {
    showWelcome();
  }
}

/* ------------------------------------------------------------------- data */

async function loadState() {
  renderState(await api("/api/state"));
}

async function loadLibrary() {
  const payload = await api("/api/sessions");
  app.sessions = payload.sessions || [];
  app.folders = payload.folders || [];
  renderSider();
}

async function loadHistory() {
  const payload = await api("/api/history");
  app.messages = payload.messages || [];
  if (payload.session && app.state) app.state.session = payload.session;
  renderConversation();
  if (app.state && app.state.session) {
    dom.viewTitle.textContent = app.state.session.title || "新对话";
  }
}

/* ---------------------------------------------------------- folder actions */

function applyLibrary(payload) {
  if (payload.state) renderState(payload.state);
  app.sessions = payload.sessions || [];
  app.folders = payload.folders || [];
  renderSider();
}

async function deleteSession(session) {
  if (app.streaming) return;
  if (!window.confirm(`删除对话「${session.title || "新对话"}」？删除后无法恢复。`)) return;
  const wasOpen = Boolean(app.state && app.state.session && app.state.session.id === session.id);
  try {
    applyLibrary(await api("/api/session/delete", { id: session.id }));
    if (wasOpen) {
      app.messages = [];
      clearMessages();
      showWelcome();
    }
    toast("对话已删除");
  } catch (error) {
    toast(`删除失败：${error.message}`);
  }
}

async function moveSession(session, folder) {
  if (app.streaming) return;
  try {
    applyLibrary(await api("/api/session/folder", { id: session.id, folder: folder || null }));
    toast(folder ? "已移入文件夹" : "已移出文件夹");
  } catch (error) {
    toast(`移动失败：${error.message}`);
  }
}

async function askFolderName(title, value) {
  const answer = window.prompt(title, value || "");
  if (answer === null) return null;
  const name = answer.trim();
  if (!name) {
    toast("文件夹名称不能为空");
    return null;
  }
  return name;
}

async function createFolder() {
  if (app.streaming) return null;
  const name = await askFolderName("新建文件夹名称");
  if (!name) return null;
  try {
    const payload = await api("/api/folder", { name });
    applyLibrary(payload);
    toast("文件夹已创建");
    return payload.folder || null;
  } catch (error) {
    toast(`新建失败：${error.message}`);
    return null;
  }
}

async function createFolderAndMove(session) {
  const folder = await createFolder();
  if (folder) await moveSession(session, folder.id);
}

async function renameFolder(folder) {
  if (app.streaming) return;
  const name = await askFolderName("重命名文件夹", folder.name);
  if (!name || name === folder.name) return;
  try {
    applyLibrary(await api("/api/folder", { id: folder.id, name }));
    toast("文件夹已重命名");
  } catch (error) {
    toast(`重命名失败：${error.message}`);
  }
}

async function deleteFolder(folder) {
  if (app.streaming) return;
  const hint = `删除文件夹「${folder.name}」？里面的对话会移到「未分类」。`;
  if (!window.confirm(hint)) return;
  try {
    applyLibrary(await api("/api/folder/delete", { id: folder.id }));
    toast("文件夹已删除");
  } catch (error) {
    toast(`删除失败：${error.message}`);
  }
}

async function openSession(id) {
  if (app.streaming) return;
  try {
    renderState(await api("/api/session", { id }));
    await loadHistory();
    renderSider();
    dom.viewTitle.textContent = (app.state.session && app.state.session.title) || "新对话";
    closeDrawer();
  } catch (error) {
    toast(`对话加载失败，请重试：${error.message}`);
  }
}

async function newChat() {
  if (app.streaming) return;
  try {
    renderState(await api("/api/new", {}));
    app.messages = [];
    clearMessages();
    showWelcome();
    await loadLibrary();
    dom.prompt.focus();
  } catch (error) {
    toast(`新建对话失败：${error.message}`);
  }
}

/* ------------------------------------------------------------------- chat */

function autoGrow() {
  const prompt = dom.prompt;
  prompt.style.height = "auto";
  prompt.style.height = `${Math.min(prompt.scrollHeight, 336)}px`;
}

async function send() {
  const text = dom.prompt.value.trim();
  if (!text || app.streaming) return;
  dom.prompt.value = "";
  autoGrow();

  showSessionView();
  addUserMessage(text);
  app.messages.push({ role: "user", content: text });

  const turn = createAssistantMessage();
  app.streaming = true;
  app.turn = turn;
  updateSendState();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (!response.ok || !response.body) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const payload = JSON.parse(await response.text());
        if (payload.error) message = payload.error;
      } catch {
        /* 不是 JSON 就保留状态行 */
      }
      throw new Error(message);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index = buffer.indexOf("\n\n");
      while (index >= 0) {
        const frame = parseFrame(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        if (frame) applyEvent(frame.event, frame.payload, turn);
        index = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) {
      const frame = parseFrame(buffer);
      if (frame) applyEvent(frame.event, frame.payload, turn);
    }
  } catch (error) {
    turn.waiting.remove();
    turn.notice.hidden = false;
    turn.notice.textContent = error.message;
  } finally {
    app.streaming = false;
    app.turn = null;
    if (app.state) app.state.busy = false;
    updateSendState();
    try {
      await loadState();
      dom.viewTitle.textContent = (app.state.session && app.state.session.title) || "新对话";
      await loadLibrary();
    } catch {
      /* 刷新失败不影响已经拿到的回答 */
    }
  }
}

/* --------------------------------------------------------------- settings */

function currentProvider(name) {
  if (!app.state) return null;
  return (app.state.providers || []).find((item) => item.name === name) || null;
}

function fillProviderFields() {
  const provider = currentProvider(dom.providerSelect.value);
  if (!provider) return;
  dom.protocolSelect.value = provider.protocol;
  const options = dom.modelOptions;
  options.replaceChildren();
  for (const model of provider.models || []) {
    const option = document.createElement("option");
    option.value = model;
    options.append(option);
  }
  const isCurrent = app.state && provider.name === app.state.provider;
  dom.modelInput.value = isCurrent && app.state.model ? app.state.model : (provider.models || [])[0] || "";
  dom.apiKeySaved.hidden = !provider.has_api_key;
  dom.apiKeyInput.hidden = Boolean(provider.has_api_key);
  if (!provider.has_api_key) dom.keyInput.value = "";
  const keyHint = provider.has_api_key
    ? "已配置 API Key（来自设置文件或环境变量）"
    : provider.local
      ? "本地服务通常不需要 API Key"
      : `还没有 API Key${provider.api_key_env ? `，也可以设置环境变量 ${provider.api_key_env}` : ""}`;
  dom.providerDescription.textContent = provider.base_url || "";
  dom.apiKeyHint.textContent = keyHint;
}

function switchPanel(panel) {
  app.tab = panel;
  for (const button of dom.settingsNav.querySelectorAll(".nav-item")) {
    button.classList.toggle("nav-item-active", button.dataset.panel === panel);
  }
  for (const section of dom.settingsContent.querySelectorAll(".settings-panel")) {
    section.hidden = section.dataset.panel !== panel;
  }
}

function markThemeOptions() {
  for (const option of dom.settingsOverlay.querySelectorAll(".theme-option")) {
    option.classList.toggle("theme-option-selected", option.dataset.theme === app.theme);
  }
}

function openSettings(panel) {
  if (!app.state) return;
  dom.providerSelect.replaceChildren();
  for (const provider of app.state.providers || []) {
    const option = document.createElement("option");
    option.value = provider.name;
    option.textContent = provider.local ? `${provider.name}（本地）` : provider.name;
    dom.providerSelect.append(option);
  }
  dom.providerSelect.value = app.state.provider;
  dom.protocolSelect.replaceChildren();
  for (const name of app.state.protocols || []) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = PROTOCOL_LABELS[name] || name;
    dom.protocolSelect.append(option);
  }
  dom.settingsPath.textContent = app.state.settings_path || "";
  dom.workspaceDescription.textContent = app.state.workspace || "";
  fillProviderFields();
  switchPanel(panel || app.tab);
  markThemeOptions();
  dom.settingsOverlay.hidden = false;
}

function closeSettings() {
  dom.settingsOverlay.hidden = true;
  dom.keyInput.value = "";
}

async function saveSettings() {
  const payload = {
    provider: dom.providerSelect.value,
    protocol: dom.protocolSelect.value,
    model: dom.modelInput.value.trim(),
  };
  const key = dom.keyInput.value.trim();
  if (key) payload.api_key = key;
  dom.settingsSave.disabled = true;
  try {
    renderState(await api("/api/settings", payload));
    closeSettings();
    toast("设置已保存");
    await loadLibrary();
  } catch (error) {
    dom.apiKeyHint.textContent = `保存失败：${error.message}`;
  } finally {
    dom.settingsSave.disabled = false;
  }
}

/* ----------------------------------------------------------------- folder */

async function openFolder(path) {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  let listing;
  try {
    listing = await api(`/api/browse${query}`);
  } catch (error) {
    dom.folderHint.textContent = error.message;
    return;
  }
  app.browsing = listing;
  dom.folderPath.value = listing.path;
  dom.folderUp.disabled = !listing.parent;
  dom.folderHint.textContent = "";
  dom.folderList.replaceChildren();
  if (!listing.directories.length) {
    const empty = el("li", "folder-empty");
    empty.textContent = "没有子文件夹";
    dom.folderList.append(empty);
  }
  for (const entry of listing.directories) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = entry.name;
    button.addEventListener("click", () => openFolder(entry.path));
    item.append(button);
    dom.folderList.append(item);
  }
  dom.folderRoots.replaceChildren();
  for (const root of listing.roots || []) {
    const button = el("button", "button outlined");
    button.type = "button";
    button.textContent = root;
    button.addEventListener("click", () => openFolder(root));
    dom.folderRoots.append(button);
  }
}

function showFolderDialog() {
  if (app.state) dom.folderPath.value = app.state.workspace || "";
  dom.folderOverlay.hidden = false;
  openFolder(app.state ? app.state.workspace : "");
}

async function useFolder() {
  // 输入框里的路径优先：浏览过别的目录之后手打一个路径，用的是手打的那个。
  const typed = dom.folderPath.value.trim();
  const path = typed || (app.browsing ? app.browsing.path : "");
  try {
    renderState(await api("/api/workspace", { path }));
    dom.folderOverlay.hidden = true;
    app.messages = [];
    clearMessages();
    showWelcome();
    await loadHistory();
    await loadLibrary();
    toast("已切换工作目录");
  } catch (error) {
    dom.folderHint.textContent = error.message;
  }
}

/* ------------------------------------------------------------------ theme */

function resolvedTheme() {
  if (app.theme === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return app.theme;
}

function applyTheme() {
  const theme = resolvedTheme();
  if (theme === "dark") document.body.setAttribute("data-ds-dark-theme", "");
  else document.body.removeAttribute("data-ds-dark-theme");
  markThemeOptions();
}

/* ------------------------------------------------------------------- boot */

function cacheDom() {
  dom.sider = $("sider");
  dom.siderMask = $("sider-mask");
  dom.mobileMenu = $("mobile-menu");
  dom.siderToggle = $("sider-toggle");
  dom.newChat = $("new-chat");
  dom.siderList = $("sider-list");
  dom.openSettings = $("open-settings");
  dom.newFolder = $("new-folder");
  dom.welcome = $("welcome");
  dom.welcomeInput = $("welcome-input");
  dom.view = $("view");
  dom.viewTitle = $("view-title");
  dom.messageList = $("message-list");
  dom.messages = $("messages");
  dom.sessionInput = $("session-input");
  dom.composer = $("composer");
  dom.prompt = $("prompt");
  dom.thinkingToggle = $("thinking-toggle");
  dom.workspaceToggle = $("workspace-toggle");
  dom.workspaceLabel = $("workspace-label");
  dom.send = $("send");
  dom.settingsOverlay = $("settings-overlay");
  dom.settingsNav = dom.settingsOverlay.querySelector(".settings-nav");
  dom.settingsContent = dom.settingsOverlay.querySelector(".settings-content");
  dom.settingsClose = $("settings-close");
  dom.settingsCancel = $("settings-cancel");
  dom.settingsSave = $("settings-save");
  dom.providerSelect = $("provider-select");
  dom.protocolSelect = $("protocol-select");
  dom.modelInput = $("model-input");
  dom.modelOptions = $("model-options");
  dom.providerDescription = $("provider-description");
  dom.apiKeySaved = $("api-key-saved");
  dom.apiKeyInput = $("api-key-input");
  dom.apiKeyChange = $("api-key-change");
  dom.apiKeyConfirm = $("api-key-confirm");
  dom.keyInput = $("key-input");
  dom.apiKeyHint = $("api-key-hint");
  dom.workspaceDescription = $("workspace-description");
  dom.changeWorkspace = $("change-workspace");
  dom.settingsPath = $("settings-path");
  dom.folderOverlay = $("folder-overlay");
  dom.folderClose = $("folder-close");
  dom.folderCancel = $("folder-cancel");
  dom.folderUse = $("folder-use");
  dom.folderUp = $("folder-up");
  dom.folderPath = $("folder-path");
  dom.folderGo = $("folder-go");
  dom.folderRoots = $("folder-roots");
  dom.folderList = $("folder-list");
  dom.folderHint = $("folder-hint");
  dom.toast = $("toast");
}

function bindEvents() {
  dom.prompt.addEventListener("input", autoGrow);
  dom.prompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
  dom.send.addEventListener("click", send);
  dom.newChat.addEventListener("click", () => {
    closeDrawer();
    newChat();
  });
  dom.newFolder.addEventListener("click", () => {
    closeDrawer();
    createFolder();
  });
  dom.openSettings.addEventListener("click", () => {
    closeDrawer();
    openSettings("model");
  });
  dom.mobileMenu.addEventListener("click", () => {
    if (isNarrow()) {
      // 窄屏的边栏是抽屉。宽屏收起过之后 .sider 还带着 hidden，先恢复，
      // 否则抽屉被 display:none 挡住，点了也看不见。
      if (app.siderHidden) setSiderHidden(false);
      openDrawer();
      return;
    }
    setSiderHidden(false);
  });
  dom.siderMask.addEventListener("click", closeDrawer);
  dom.siderToggle.addEventListener("click", () => {
    if (isNarrow()) {
      closeDrawer();
      return;
    }
    setSiderHidden(!app.siderHidden);
  });
  dom.thinkingToggle.addEventListener("click", async () => {
    if (app.streaming) return;
    const next = !dom.thinkingToggle.classList.contains("selected");
    try {
      renderState(await api("/api/thinking", { enabled: next }));
    } catch (error) {
      toast(`切换失败：${error.message}`);
    }
  });
  dom.workspaceToggle.addEventListener("click", showFolderDialog);

  for (const button of dom.settingsNav.querySelectorAll(".nav-item")) {
    button.addEventListener("click", () => switchPanel(button.dataset.panel));
  }
  for (const option of dom.settingsOverlay.querySelectorAll(".theme-option")) {
    option.addEventListener("click", () => {
      app.theme = option.dataset.theme;
      storageSet("minimalcode-theme", app.theme);
      applyTheme();
    });
  }
  dom.providerSelect.addEventListener("change", fillProviderFields);
  dom.providerSelect.addEventListener("change", fillProviderFields);
  dom.apiKeyChange.addEventListener("click", () => {
    dom.apiKeySaved.hidden = true;
    dom.apiKeyInput.hidden = false;
    dom.keyInput.value = "";
    dom.keyInput.focus();
  });
  dom.apiKeyConfirm.addEventListener("click", saveSettings);
  dom.keyInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      saveSettings();
    }
  });
  dom.settingsSave.addEventListener("click", saveSettings);
  dom.settingsCancel.addEventListener("click", closeSettings);
  dom.settingsClose.addEventListener("click", closeSettings);
  dom.changeWorkspace.addEventListener("click", () => {
    closeSettings();
    showFolderDialog();
  });

  dom.folderClose.addEventListener("click", () => {
    dom.folderOverlay.hidden = true;
  });
  dom.folderCancel.addEventListener("click", () => {
    dom.folderOverlay.hidden = true;
  });
  dom.folderUse.addEventListener("click", useFolder);
  dom.folderUp.addEventListener("click", () => {
    if (app.browsing && app.browsing.parent) openFolder(app.browsing.parent);
  });
  dom.folderGo.addEventListener("click", () => openFolder(dom.folderPath.value.trim()));
  dom.folderPath.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      openFolder(dom.folderPath.value.trim());
    }
  });

  dom.messages.addEventListener("click", (event) => {
    const button = event.target.closest(".md-code-block-copy");
    if (!button) return;
    const block = button.closest(".md-code-block");
    const code = block ? block.querySelector("code") : null;
    if (code) copyText(code.textContent || "");
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (app.theme === "system") applyTheme();
  });
  // 浮层菜单贴在按钮旁边，滚动或缩放后位置就不再成立，直接收起。
  document.addEventListener("click", (event) => {
    if (app.menu && !app.menu.contains(event.target)) closeMenu();
  });
  dom.siderList.addEventListener("scroll", closeMenu);
  window.addEventListener("resize", closeMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (app.menu) closeMenu();
    else if (!dom.folderOverlay.hidden) dom.folderOverlay.hidden = true;
    else if (!dom.settingsOverlay.hidden) closeSettings();
  });
}

function refreshBusyState() {
  // 刷新页面时正好有一轮在跑：/api/state 的 busy 会一直停在 true，发送按钮
  // 就永远灰着。轮询到这一轮结束为止，其它状态照旧。
  if (!app.state || !app.state.busy) return;
  setTimeout(async () => {
    try {
      renderState(await api("/api/state"));
    } catch {
      return;
    }
    refreshBusyState();
  }, 1500);
}

async function boot() {
  cacheDom();
  applyTheme();
  bindEvents();
  autoGrow();
  try {
    await loadState();
    await loadLibrary();
    await loadHistory();
  } catch (error) {
    toast(`无法连接本地服务：${error.message}`);
  }
  refreshBusyState();
  dom.prompt.focus();
}

boot();
