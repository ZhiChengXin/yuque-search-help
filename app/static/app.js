const $ = (id) => document.getElementById(id);

const MODE_META = {
  all: {
    hint: "直接描述问题，例如「特定邮件打开导致网页崩溃」",
    placeholder: "描述问题、报错现象或功能名…",
    suggestTitle: "试试这些问题",
    suggestions: [
      ["特定邮件打开导致网页崩溃", "打开邮件时页面崩溃，怎么排查？"],
      ["邮箱绑定到孚盟邮后，邮件为什么没有完全同步", "绑定后邮件同步不全"],
      ["绑定邮箱", "个人/企业邮箱如何绑定"],
      ["千里眼", "如何跟踪客户是否查看邮件"],
      ["草稿邮件打开后内容为空", "草稿打开后内容丢失"],
      ["孚盟邮", "查找孚盟邮模块测试与产品对接人"],
    ],
  },
  faq: {
    hint: "输入故障现象或报错原文，优先匹配问题排查类内容",
    placeholder: "输入故障现象，如「邮件打不开」「同步失败」…",
    suggestTitle: "常见故障",
    suggestions: [
      ["特定邮件打开导致网页崩溃", "特定邮件打开导致网页崩溃"],
      ["邮件内容无法显示", "邮件内容无法显示 / 无法正常查看"],
      ["草稿邮件打开后内容为空", "草稿邮件打开后内容为空"],
      ["手机端夜间时段邮件经常无法显示", "手机端夜间时段收不到信"],
      ["邮箱绑定到孚盟邮后，邮件为什么没有完全同步", "绑定后同步不全"],
      ["孚盟邮发邮件，为什么提示退信", "发信提示退信怎么办"],
    ],
  },
  manual: {
    hint: "输入操作关键词，例如「绑定邮箱」「签名管理」「定时发送」",
    placeholder: "输入操作步骤关键词，如「绑定邮箱」「签名管理」…",
    suggestTitle: "操作指引",
    suggestions: [
      ["绑定邮箱", "如何绑定个人/企业邮箱"],
      ["签名管理", "如何设置和使用邮件签名"],
      ["定时发送", "如何设置定时/延时发送"],
      ["模板管理", "如何创建和使用邮件模板"],
      ["千里眼", "如何开启和使用千里眼"],
      ["自动回复", "如何设置个人假期自动回复"],
    ],
  },
  owner: {
    hint: "输入一级/二级菜单名，查找对应测试与产品联系人，例如「孚盟邮」「孚盟邮件」",
    placeholder: "输入菜单或模块名，如「孚盟邮」「WhatsApp」…",
    suggestTitle: "常用模块",
    suggestions: [
      ["孚盟邮", "查找孚盟邮相关对接人"],
      ["孚盟邮件", "查找孚盟邮件模块负责人"],
      ["WhatsApp", "查找 WhatsApp 模块负责人"],
      ["AI助手", "查找 AI 助手相关对接人"],
      ["工作台", "查找工作台模块负责人"],
      ["邮件营销", "查找邮件营销模块负责人"],
    ],
  },
};

let currentMode = "all";
let lastSuggestReadyKey = "";

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function extractKeywords(query) {
  let q = String(query || "").trim();
  if (!q) return [];
  q = q.replace(/^Q[:：\s]+/i, "").replace(/[？?！!。．.]+$/g, "");

  const stop = new Set([
    "为什么", "怎么", "如何", "什么", "一个", "一下", "没有", "可以",
    "或者", "以及", "如果", "还有", "这个", "那个", "进行", "相关",
    "设置", "使用", "进行", "查看", "打开", "点击",
  ]);
  const parts = [];

  // 整句优先
  if (q.length >= 2) parts.push(q);

  // 仅按标点切开，避免把整词拆得过碎
  q.split(/[\s，。？！、；：,.!?;:（）()【】\[\]《》"'“”/\\|+]+/)
    .map((p) => p.trim())
    .filter((p) => p.length >= 3)
    .forEach((p) => parts.push(p));

  // 仅补强较长业务词，避免「邮箱」这类短词刷屏
  ["孚盟邮", "孚盟邮件", "授权码", "千里眼", "WhatsApp"].forEach((w) => {
    if (q.includes(w) && w.length >= 3) parts.push(w);
  });

  const seen = new Set();
  const uniq = [];
  parts
    .filter((p) => p && !stop.has(p))
    .sort((a, b) => b.length - a.length)
    .forEach((p) => {
      const key = p.toLowerCase();
      if (seen.has(key)) return;
      // 已被更长词覆盖的短词不再单独高亮
      if (uniq.some((u) => u.toLowerCase().includes(key))) return;
      seen.add(key);
      uniq.push(p);
    });
  return uniq.slice(0, 4);
}

function highlight(text, query) {
  const source = String(text || "");
  if (!source) return "";
  const keywords = extractKeywords(query);
  if (!keywords.length) return escapeHtml(source);

  try {
    const escaped = keywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const re = new RegExp(`(${escaped.join("|")})`, "gi");
    return source
      .split(re)
      .map((part) => {
        if (!part) return "";
        const hit = keywords.some((k) => k.toLowerCase() === part.toLowerCase());
        return hit ? `<mark>${escapeHtml(part)}</mark>` : escapeHtml(part);
      })
      .join("");
  } catch {
    return escapeHtml(source);
  }
}

function setStatus(title, text, mode = "loading") {
  $("statusTitle").textContent = title;
  $("statusText").textContent = text;
  const dot = $("statusDot");
  dot.classList.remove("ok", "err");
  if (mode === "ok") dot.classList.add("ok");
  if (mode === "err") dot.classList.add("err");
}

function setNotice(title, text) {
  const box = $("noticeBox");
  if (!title) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(text || "")}`;
}

function peopleChips(text) {
  return String(text || "")
    .split(/[、，,;；/|]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function paintSuggestions(items) {
  $("suggestGrid").innerHTML = (items || [])
    .map((item, index) => {
      const testers = peopleChips(item.testers);
      const products = peopleChips(item.product_owner);
      const isOwner = item.kind === "owner" || testers.length || products.length;
      const peopleHtml =
        testers.length || products.length
          ? `<div class="suggest-people">
              ${
                testers.length
                  ? `<div class="people-row"><span>测试</span><div class="name-chips">${testers
                      .map((n) => `<em>${escapeHtml(n)}</em>`)
                      .join("")}</div></div>`
                  : ""
              }
              ${
                products.length
                  ? `<div class="people-row"><span>产品</span><div class="name-chips">${products
                      .map((n) => `<em>${escapeHtml(n)}</em>`)
                      .join("")}</div></div>`
                  : ""
              }
            </div>`
          : `<em>${escapeHtml(item.desc || "点击即可检索")}</em>`;
      return `
      <button type="button" class="suggest-card${isOwner ? " owner-card" : ""}" data-q="${escapeHtml(item.q)}" data-id="${escapeHtml(item.id || "")}">
        <span class="suggest-no">${String(index + 1).padStart(2, "0")}</span>
        <strong>${escapeHtml(item.q)}</strong>
        ${item.menu ? `<small class="suggest-menu">${escapeHtml(item.menu)}</small>` : ""}
        ${peopleHtml}
      </button>`;
    })
    .join("");
}

function fallbackSuggestions(mode) {
  const meta = MODE_META[mode] || MODE_META.all;
  return (meta.suggestions || []).map(([q, desc]) => ({ q, desc }));
}

function renderSuggestions(mode, items) {
  const meta = MODE_META[mode] || MODE_META.all;
  $("suggestTitle").textContent = meta.suggestTitle || "试试这些问题";
  if ($("suggestSub")) {
    $("suggestSub").hidden = true;
    $("suggestSub").textContent = "";
  }
  paintSuggestions(items && items.length ? items : fallbackSuggestions(mode));
}

function searchBtnIconHtml() {
  return `<svg class="search-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
    <path fill="currentColor" d="M10.5 3a7.5 7.5 0 0 1 5.9 12.1l4.25 4.25a1 1 0 0 1-1.4 1.4l-4.26-4.24A7.5 7.5 0 1 1 10.5 3zm0 2a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11z"/>
  </svg>`;
}

function setSearchBtnLoading(loading) {
  const btn = $("searchBtn");
  if (!btn) return;
  btn.disabled = Boolean(loading);
  btn.classList.toggle("is-loading", Boolean(loading));
  btn.innerHTML = searchBtnIconHtml();
  btn.setAttribute("aria-label", loading ? "检索中" : "检索");
  btn.title = loading ? "检索中" : "检索";
}

async function loadSuggestions(mode, { spinning = false } = {}) {
  const target = mode || currentMode;
  const refreshBtn = $("suggestRefresh");
  if (spinning && refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.classList.add("is-loading");
  }
  if (!spinning) {
    renderSuggestions(target, fallbackSuggestions(target));
  }
  try {
    const res = await fetch(
      `/api/suggest?mode=${encodeURIComponent(target)}&limit=6&_=${Date.now()}`
    );
    const data = await res.json();
    if (target !== currentMode) return;
    if (data.ok && Array.isArray(data.items) && data.items.length) {
      renderSuggestions(target, data.items);
    } else if (spinning) {
      renderSuggestions(target, fallbackSuggestions(target));
    }
  } catch {
    // 保持兜底示例
  } finally {
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.classList.remove("is-loading");
    }
  }
}

function showIntroNote(show) {
  $("emptyState").hidden = !show;
}

function clearResultViews() {
  $("results").innerHTML = "";
  $("contacts").hidden = true;
  $("contacts").innerHTML = "";
  $("adviceBox").hidden = true;
  $("adviceBox").textContent = "";
  $("metaRow").hidden = true;
  $("resultMeta").textContent = "";
  setNotice("", "");
}

function resetSearchPanel() {
  $("q").value = "";
  clearResultViews();
  showIntroNote(true);
}

function restoreIdleState() {
  clearResultViews();
  showIntroNote(true);
  loadSuggestions(currentMode);
}

function applyMode(mode, { clearInput = false } = {}) {
  currentMode = mode;
  document.querySelectorAll(".mode").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  $("searchStage").dataset.mode = mode;
  const meta = MODE_META[mode] || MODE_META.all;
  $("searchHint").textContent = meta.hint;
  $("q").placeholder = meta.placeholder || MODE_META.all.placeholder;
  loadSuggestions(mode);
  if (clearInput) {
    resetSearchPanel();
  }
}

function filterByMode(results) {
  // FAQ / 手册 / 智能检索共用同一后端检索；侧栏只做引导，不再前端硬过滤漏结果
  return results;
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.syncing) {
      setStatus("同步中", "正在拉取最新文档与分工表…", "loading");
    } else if (data.ready) {
      setStatus(
        "已就绪",
        `${data.doc_count || 0} 篇文档 · ${data.section_count || 0} 个章节 · ${data.contact_count || 0} 位联系人${
          data.vector_enabled ? ` · 向量 ${data.vector_count || 0}` : ""
        }`,
        "ok"
      );
      const readyKey = `${data.last_synced_at || 0}:${data.section_count || 0}:${data.contact_count || 0}`;
      if (readyKey !== lastSuggestReadyKey) {
        lastSuggestReadyKey = readyKey;
        if (!$("emptyState").hidden) {
          loadSuggestions(currentMode);
        }
      }
    } else if (data.last_error) {
      setStatus("同步失败", data.last_error, "err");
    } else {
      setStatus("准备中", "等待首次同步…", "loading");
    }
    return data;
  } catch {
    setStatus("离线", "无法连接本地服务", "err");
    return null;
  }
}

function renderContacts(contacts, query = "") {
  const box = $("contacts");
  if (!contacts || !contacts.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.classList.add("contacts-suggest");
  box.innerHTML = contacts
    .map((c, index) => {
      const title = c.module || c.menu || "模块负责人";
      const menu = [c.menu, c.business_line].filter(Boolean).join(" / ");
      const testers = peopleChips(c.testers);
      const products = peopleChips(c.product_owner);
      const line = (c.business_line || "").trim() || "-";
      return `
      <article class="suggest-card owner-card contact-result-card" style="animation-delay:${index * 40}ms">
        <span class="suggest-no">${String(index + 1).padStart(2, "0")}</span>
        <strong>${highlight(title, query)}</strong>
        ${menu ? `<small class="suggest-menu">${highlight(menu, query)}</small>` : ""}
        <div class="suggest-people">
          <div class="people-row">
            <span>测试负责人</span>
            <div class="name-chips">${
              testers.length
                ? testers.map((n) => `<em>${escapeHtml(n)}</em>`).join("")
                : "<em class=\"empty\">暂未登记</em>"
            }</div>
          </div>
          <div class="people-row">
            <span>产品负责人</span>
            <div class="name-chips">${
              products.length
                ? products.map((n) => `<em>${escapeHtml(n)}</em>`).join("")
                : "<em class=\"empty\">暂未登记</em>"
            }</div>
          </div>
          <div class="people-row">
            <span>业务线</span>
            <div class="name-chips"><em>${highlight(line, query)}</em></div>
          </div>
        </div>
      </article>`;
    })
    .join("");
}

function loadingHtml(text = "正在检索…") {
  const sub =
    text.includes("匹配") ? "正在查找对应测试与产品对接人" : "稍等片刻，马上就好";
  return `<div class="loading-state" aria-live="polite">
    <div class="loading-spinner" aria-hidden="true"></div>
    <strong>${escapeHtml(text)}</strong>
    <span>${escapeHtml(sub)}</span>
  </div>`;
}

function renderResults(query, payload) {
  const results = filterByMode(payload.results || []);
  const contacts = payload.contacts || [];
  const confidence = payload.confidence || "none";
  const advice = payload.advice || "";
  const suggestFeedback = Boolean(payload.suggest_feedback);
  const isOwnerMode = currentMode === "owner";

  const adviceBox = $("adviceBox");
  const ownerDocHint = Boolean(payload.owner_doc_hint);
  // 已命中文档时，下方结果卡本身就是答案，不再展示「已定位到…」类提示
  // 负责人模式：只有问到文档类问题时展示引导文案
  if ((results.length && !suggestFeedback && !isOwnerMode) || (isOwnerMode && !ownerDocHint)) {
    adviceBox.hidden = true;
    adviceBox.textContent = "";
  } else {
    adviceBox.hidden = !advice;
    adviceBox.className = `advice ${confidence}`;
    adviceBox.textContent = advice;
  }

  $("metaRow").hidden = false;
  if (isOwnerMode) {
    $("resultMeta").textContent = `「${query}」· 联系人 ${contacts.length}`;
  } else {
    $("resultMeta").textContent = `「${query}」· 文档命中 ${results.length}`;
  }

  if (isOwnerMode) {
    renderContacts(contacts, query);
    $("results").innerHTML = "";
    showIntroNote(false);
    if (!contacts.length && !ownerDocHint) {
      setNotice("未匹配到模块负责人", "试试一级/二级菜单名，例如「孚盟邮」「孚盟邮件」「WhatsApp」。");
    } else {
      setNotice("", "");
    }
    return;
  }

  if (!results.length) {
    renderContacts(contacts, query);
    const missHint = contacts.length
      ? "可先联系下方相关模块负责人确认，或把问题留下我们补充资料。"
      : "你可以换个说法再搜，或把问题留下，我们后续补资料。";
    $("results").innerHTML = `
      <div class="miss-card">
        <h3>暂时没找到对应文档</h3>
        <p>${missHint}</p>
        <button type="button" class="feedback-inline-btn" id="missFeedbackBtn">提交这个问题反馈</button>
      </div>`;
    showIntroNote(false);
    if (contacts.length) {
      setNotice("已找到可联系的人", "可直接联系对应测试/产品确认。");
    } else {
      setNotice("", "");
    }
    const missBtn = $("missFeedbackBtn");
    if (missBtn) {
      missBtn.addEventListener("click", () => openFeedbackWithQuery(query));
    }
    return;
  }

  renderContacts([]);
  setNotice("", "");
  showIntroNote(false);
  $("results").innerHTML = results
    .map((item, index) => {
      const path = (item.catalog_path || []).join(" / ");
      const openUrl = item.url || item.doc_url;
      const openLabel = "查看该章节";
      const answerText = String(item.answer || "").trim();
      const previewText = item.preview || item.snippet || "";
      const images = Array.isArray(item.images) ? item.images.slice(0, 2) : [];
      const imageHtml = images.length
        ? `<div class="preview-images">${images
            .map(
              (src) => `
            <button type="button" class="preview-image" data-fullsrc="${escapeHtml(src)}" title="查看大图">
              <img src="${escapeHtml(src)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
            </button>`
            )
            .join("")}</div>`
        : "";
      const moreHint =
        !answerText && (item.truncated || (item.image_count || 0) > images.length)
          ? `<p class="preview-more">内容较多，已折叠展示部分正文${
              (item.image_count || 0) > images.length
                ? `与图片（共 ${item.image_count} 张）`
                : ""
            }，可打开章节查看完整内容。</p>`
          : "";
      const matchPercent = Number(item.match_percent);
      const matchLabel = Number.isFinite(matchPercent)
        ? `匹配度 ${matchPercent.toFixed(1)}%`
        : item.match_hint || "相关";
      const isBest = index === 0;
      const badgeClass = isBest || matchPercent >= 70 ? "badge match-high" : "badge match-mid";
      const category = (item.catalog_path || []).slice(-1)[0] || item.doc_title || "";
      const answerHtml = answerText
        ? `<div class="answer-hit${isBest ? " is-best" : ""}">
            <span>解答</span>
            <p>${highlight(answerText, query)}</p>
          </div>`
        : `<div class="snippet-label">原文摘录</div>
           <div class="snippet">${highlight(previewText, query)}</div>`;
      return `
        <article class="result-card${isBest ? " best" : ""}" style="animation-delay:${index * 40}ms">
          <div class="top">
            <div class="title-wrap">
              ${category ? `<span class="category">${isBest ? "🏆 " : ""}${escapeHtml(category)}</span>` : ""}
              <h3><a href="${escapeHtml(openUrl)}" target="_blank" rel="noopener">${highlight(item.title, query)}</a></h3>
            </div>
            <span class="${badgeClass}">${escapeHtml(matchLabel)}</span>
          </div>
          <div class="doc-title">所属文档：${escapeHtml(item.doc_title || "")}</div>
          ${path ? `<div class="path">${escapeHtml(path)}</div>` : ""}
          ${answerHtml}
          ${imageHtml}
          ${moreHint}
          <div class="card-actions">
            <a class="primary" href="${escapeHtml(openUrl)}" target="_blank" rel="noopener">${openLabel}</a>
          </div>
        </article>`;
    })
    .join("");
}

function openFeedbackWithQuery(query) {
  const seed = (query || $("q").value || "").trim();
  openFeedback({ seedText: seed });
}

const MISS_SEARCH_ADVICE =
  "这边暂时没翻到能直接对上的资料，可能相关文档还没同步进来。" +
  "你可以换个关键词再试试，或点「我要反馈」把问题留下，我们后续补上。";

/** 与「未找到文档」同一套页面：顶部提示 + 文档命中 0 + 反馈卡片 */
function renderMissResults(query, { advice = MISS_SEARCH_ADVICE, contacts = [] } = {}) {
  renderResults(query, {
    results: [],
    contacts,
    confidence: "none",
    advice,
    suggest_feedback: true,
  });
}

async function doSearch(query, { fast = false } = {}) {
  const q = (query || "").trim();
  if (!q) return;
  const isOwner = currentMode === "owner";
  // 负责人模式永远本地匹配；文档检索默认先走快路径（字面命中秒回）
  const useFast = isOwner || fast;
  const waitText = isOwner ? "正在匹配…" : "正在检索…";
  $("q").value = q;
  setSearchBtnLoading(true);
  showIntroNote(false);
  setNotice("", "");
  $("results").innerHTML = loadingHtml(waitText);
  $("contacts").hidden = true;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), isOwner ? 8000 : 35000);
  try {
    const res = await fetch(
      `/api/search?q=${encodeURIComponent(q)}&limit=5&mode=${encodeURIComponent(currentMode)}&fast=${useFast ? "1" : "0"}`,
      { signal: controller.signal }
    );
    const data = await res.json();
    if (!data.ok && !(data.results || []).length && !(data.contacts || []).length) {
      $("adviceBox").hidden = false;
      $("adviceBox").className = "advice none";
      $("adviceBox").textContent = data.message || "暂时无法搜索";
      $("results").innerHTML = "";
      setNotice("暂时无法搜索", data.message || "请稍后重试");
      return;
    }
    renderResults(q, data);
  } catch (err) {
    const aborted = err && (err.name === "AbortError" || err.code === 20);
    if (aborted) {
      renderMissResults(q);
    } else {
      renderMissResults(q, {
        advice: "搜索请求失败，请确认服务已启动后再试，或点「我要反馈」把问题留下。",
      });
    }
  } finally {
    clearTimeout(timer);
    setSearchBtnLoading(false);
  }
}

async function openSuggestion(q, sectionId) {
  const query = (q || "").trim();
  if (!query) return;
  // 负责人模式：点推荐即匹配联系人，不用「打开文档」文案
  if (currentMode === "owner" || !sectionId) {
    await doSearch(query, { fast: true });
    return;
  }
  $("q").value = query;
  showIntroNote(false);
  setNotice("", "");
  $("results").innerHTML = loadingHtml("正在检索…");
  try {
    const res = await fetch(`/api/lookup?id=${encodeURIComponent(sectionId)}`);
    const data = await res.json();
    if (data.ok && (data.results || []).length) {
      renderResults(query, data);
      return;
    }
  } catch {
    // 回退快速检索
  }
  await doSearch(query, { fast: true });
}

$("searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  doSearch($("q").value);
});

$("q").addEventListener("input", () => {
  if (!$("q").value.trim()) {
    restoreIdleState();
    return;
  }
  showIntroNote(false);
});

$("modes").addEventListener("click", (e) => {
  const btn = e.target.closest(".mode");
  if (!btn) return;
  const next = btn.dataset.mode;
  if (next === currentMode) return;
  applyMode(next, { clearInput: true });
});

$("suggestGrid").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-q]");
  if (!btn) return;
  showIntroNote(false);
  openSuggestion(btn.dataset.q, btn.dataset.id || "");
});

$("suggestRefresh").addEventListener("click", () => {
  loadSuggestions(currentMode, { spinning: true });
});

let lightboxUserZoom = 1;
let lightboxFitScale = 1;
let lightboxNatural = { w: 0, h: 0 };
let lightboxDragging = false;
let lightboxDragStart = { x: 0, y: 0, left: 0, top: 0 };

function lightboxDisplayScale() {
  return lightboxFitScale * lightboxUserZoom;
}

function layoutLightboxImage(anchor) {
  const img = $("lightboxImg");
  const stage = $("lightboxStage");
  if (!img || !stage || $("lightbox").hidden) return;
  const nw = lightboxNatural.w || img.naturalWidth || 0;
  const nh = lightboxNatural.h || img.naturalHeight || 0;
  if (!nw || !nh) return;

  const sw = Math.max(stage.clientWidth, 1);
  const sh = Math.max(stage.clientHeight, 1);
  // 适应窗口：完整放入可视区域，小图不强制放大超过 1.2
  lightboxFitScale = Math.min(sw / nw, sh / nh, 1.2);
  const scale = lightboxDisplayScale();
  const dw = nw * scale;
  const dh = nh * scale;

  // 放大时用真实宽高撑开滚动区域，可用滚轮/拖拽浏览，不再困在固定框里
  const prevLeft = stage.scrollLeft;
  const prevTop = stage.scrollTop;
  const prevW = img.offsetWidth || dw;
  const prevH = img.offsetHeight || dh;

  img.style.width = `${dw}px`;
  img.style.height = `${dh}px`;
  img.style.maxWidth = "none";
  img.style.maxHeight = "none";
  img.style.transform = "";

  // 居中：内容小于舞台时用 margin
  const padX = Math.max(0, (sw - dw) / 2);
  const padY = Math.max(0, (sh - dh) / 2);
  img.style.marginLeft = `${padX}px`;
  img.style.marginRight = `${padX}px`;
  img.style.marginTop = `${padY}px`;
  img.style.marginBottom = `${padY}px`;

  if (anchor && prevW > 0 && prevH > 0) {
    const ratioX = (prevLeft + anchor.x) / prevW;
    const ratioY = (prevTop + anchor.y) / prevH;
    stage.scrollLeft = Math.max(0, ratioX * dw - anchor.x);
    stage.scrollTop = Math.max(0, ratioY * dh - anchor.y);
  }

  const pct = Math.round(lightboxUserZoom * 100);
  $("lightboxZoomLabel").textContent = lightboxUserZoom === 1 ? "适应" : `${pct}%`;
}

function setLightboxZoom(next, anchor) {
  lightboxUserZoom = Math.min(6, Math.max(0.4, next));
  layoutLightboxImage(anchor);
}

function openLightbox(src) {
  if (!src) return;
  lightboxUserZoom = 1;
  lightboxNatural = { w: 0, h: 0 };
  const img = $("lightboxImg");
  const stage = $("lightboxStage");
  img.onload = () => {
    lightboxNatural = { w: img.naturalWidth, h: img.naturalHeight };
    layoutLightboxImage();
    stage.scrollLeft = 0;
    stage.scrollTop = 0;
  };
  img.src = src;
  $("lightbox").hidden = false;
  document.body.style.overflow = "hidden";
  if (img.complete && img.naturalWidth) {
    lightboxNatural = { w: img.naturalWidth, h: img.naturalHeight };
    layoutLightboxImage();
  }
}

function closeLightbox() {
  $("lightbox").hidden = true;
  $("lightboxImg").removeAttribute("src");
  lightboxUserZoom = 1;
  lightboxNatural = { w: 0, h: 0 };
  document.body.style.overflow = "";
}

$("results").addEventListener("click", (e) => {
  const btn = e.target.closest(".preview-image[data-fullsrc]");
  if (!btn) return;
  e.preventDefault();
  openLightbox(btn.dataset.fullsrc);
});

$("lightboxClose").addEventListener("click", closeLightbox);
$("lightboxZoomIn").addEventListener("click", (e) => {
  e.stopPropagation();
  setLightboxZoom(lightboxUserZoom + 0.25);
});
$("lightboxZoomOut").addEventListener("click", (e) => {
  e.stopPropagation();
  setLightboxZoom(lightboxUserZoom - 0.25);
});
$("lightboxZoomFit").addEventListener("click", (e) => {
  e.stopPropagation();
  setLightboxZoom(1);
  $("lightboxStage").scrollLeft = 0;
  $("lightboxStage").scrollTop = 0;
});
$("lightboxZoomReset").addEventListener("click", (e) => {
  e.stopPropagation();
  // 原图：相对适应比例放大到 1:1 像素
  if (!lightboxFitScale) return;
  setLightboxZoom(1 / lightboxFitScale);
});

$("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox" || e.target.id === "lightboxClose") {
    closeLightbox();
  }
});

$("lightboxStage").addEventListener(
  "wheel",
  (e) => {
    if ($("lightbox").hidden) return;
    // Ctrl/⌘ + 滚轮：缩放；普通滚轮：滚动查看放大后的区域
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const rect = $("lightboxStage").getBoundingClientRect();
      const anchor = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      const delta = e.deltaY > 0 ? -0.15 : 0.15;
      setLightboxZoom(lightboxUserZoom + delta, anchor);
    }
  },
  { passive: false }
);

$("lightboxImg").addEventListener("dblclick", (e) => {
  e.preventDefault();
  if (lightboxUserZoom === 1) {
    setLightboxZoom(Math.min(3, 1 / Math.max(lightboxFitScale, 0.2)));
  } else {
    setLightboxZoom(1);
    $("lightboxStage").scrollLeft = 0;
    $("lightboxStage").scrollTop = 0;
  }
});

$("lightboxImg").addEventListener("pointerdown", (e) => {
  if (lightboxUserZoom <= 1.01) return;
  lightboxDragging = true;
  const stage = $("lightboxStage");
  lightboxDragStart = {
    x: e.clientX,
    y: e.clientY,
    left: stage.scrollLeft,
    top: stage.scrollTop,
  };
  $("lightboxImg").classList.add("is-dragging");
  $("lightboxImg").setPointerCapture(e.pointerId);
});

$("lightboxImg").addEventListener("pointermove", (e) => {
  if (!lightboxDragging) return;
  const stage = $("lightboxStage");
  stage.scrollLeft = lightboxDragStart.left - (e.clientX - lightboxDragStart.x);
  stage.scrollTop = lightboxDragStart.top - (e.clientY - lightboxDragStart.y);
});

$("lightboxImg").addEventListener("pointerup", () => {
  lightboxDragging = false;
  $("lightboxImg").classList.remove("is-dragging");
});

window.addEventListener("resize", () => {
  if ($("lightbox").hidden) return;
  layoutLightboxImage();
});

document.addEventListener("keydown", (e) => {
  if ($("lightbox").hidden) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "+" || e.key === "=") setLightboxZoom(lightboxUserZoom + 0.25);
  if (e.key === "-") setLightboxZoom(lightboxUserZoom - 0.25);
  if (e.key === "0") {
    setLightboxZoom(1);
    $("lightboxStage").scrollLeft = 0;
    $("lightboxStage").scrollTop = 0;
  }
});

$("syncBtn") && $("syncBtn").addEventListener("click", async () => {
  $("syncBtn").disabled = true;
  setStatus("同步中", "正在刷新知识库…", "loading");
  try {
    await fetch("/api/sync", { method: "POST" });
  } finally {
    await loadStatus();
    $("syncBtn").disabled = false;
  }
});

const feedbackImages = [];

function renderFeedbackShots() {
  $("feedbackShots").innerHTML = feedbackImages
    .map(
      (src, i) => `
      <div class="shot">
        <img src="${src}" alt="截图${i + 1}" />
        <button type="button" data-i="${i}" aria-label="删除">×</button>
      </div>`
    )
    .join("");
}

function resetFeedbackForm() {
  $("feedbackText").value = "";
  feedbackImages.length = 0;
  renderFeedbackShots();
  if ($("feedbackTip")) {
    $("feedbackTip").textContent = "可在输入框内粘贴截图，最多 6 张";
  }
  $("feedbackSubmit").disabled = false;
  $("feedbackSubmit").textContent = "提交反馈";
}

function openFeedback({ seedText = "" } = {}) {
  resetFeedbackForm();
  const seed = String(seedText || "").trim();
  if (seed) {
    $("feedbackText").value = seed;
  }
  $("feedbackMask").hidden = false;
  $("feedbackText").focus();
  if (seed) {
    const el = $("feedbackText");
    const len = el.value.length;
    el.setSelectionRange(len, len);
  }
}

function closeFeedback() {
  $("feedbackMask").hidden = true;
  resetFeedbackForm();
}

/** 压缩截图，显著缩短提交时上传语雀的耗时 */
function compressFeedbackImage(dataUrl, maxW = 1280, quality = 0.72) {
  return new Promise((resolve) => {
    if (!dataUrl || !String(dataUrl).startsWith("data:image/")) {
      resolve(dataUrl);
      return;
    }
    const img = new Image();
    img.onload = () => {
      try {
        let w = img.naturalWidth || img.width;
        let h = img.naturalHeight || img.height;
        if (!w || !h) {
          resolve(dataUrl);
          return;
        }
        if (w > maxW) {
          h = Math.round((h * maxW) / w);
          w = maxW;
        }
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          resolve(dataUrl);
          return;
        }
        ctx.drawImage(img, 0, 0, w, h);
        const out = canvas.toDataURL("image/jpeg", quality);
        resolve(out && out.length < String(dataUrl).length ? out : dataUrl);
      } catch {
        resolve(dataUrl);
      }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

async function addFeedbackImage(dataUrl) {
  if (!dataUrl || !String(dataUrl).startsWith("data:image/")) return false;
  if (feedbackImages.length >= 6) {
    $("feedbackTip").textContent = "最多粘贴 6 张截图";
    return false;
  }
  const compressed = await compressFeedbackImage(dataUrl);
  if (feedbackImages.length >= 6) {
    $("feedbackTip").textContent = "最多粘贴 6 张截图";
    return false;
  }
  feedbackImages.push(String(compressed));
  renderFeedbackShots();
  $("feedbackTip").textContent = `已添加截图（${feedbackImages.length}/6）`;
  return true;
}

function collectPasteImages(clipboardData) {
  const items = clipboardData && clipboardData.items;
  if (!items) return 0;
  let added = 0;
  for (const item of items) {
    if (!item.type.startsWith("image/")) continue;
    if (feedbackImages.length + added >= 6) break;
    const file = item.getAsFile();
    if (!file) continue;
    const reader = new FileReader();
    reader.onload = () => addFeedbackImage(String(reader.result || ""));
    reader.readAsDataURL(file);
    added += 1;
  }
  return added;
}

$("feedbackFab").addEventListener("click", openFeedback);
$("feedbackCancel").addEventListener("click", closeFeedback);
$("feedbackMask").addEventListener("click", (e) => {
  if (e.target.id === "feedbackMask") closeFeedback();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("feedbackMask").hidden) closeFeedback();
});
$("feedbackShots").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-i]");
  if (!btn) return;
  feedbackImages.splice(Number(btn.dataset.i), 1);
  renderFeedbackShots();
});
document.addEventListener("paste", (e) => {
  if ($("feedbackMask").hidden) return;
  if (collectPasteImages(e.clipboardData)) e.preventDefault();
});
$("feedbackForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const description = $("feedbackText").value.trim();
  if (!description && !feedbackImages.length) {
    $("feedbackTip").textContent = "请填写描述或粘贴截图";
    return;
  }
  $("feedbackSubmit").disabled = true;
  $("feedbackSubmit").textContent = "提交中…";
  try {
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        description,
        images: feedbackImages,
        query: $("q").value.trim(),
        mode: currentMode,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      $("feedbackText").value = "";
      feedbackImages.length = 0;
      renderFeedbackShots();
      $("feedbackTip").textContent = "已提交";
      setTimeout(closeFeedback, 500);
    } else {
      $("feedbackTip").textContent = data.message || "提交失败";
    }
  } catch {
    $("feedbackTip").textContent = "提交失败，请确认服务已启动";
  } finally {
    $("feedbackSubmit").disabled = false;
    $("feedbackSubmit").textContent = "提交反馈";
  }
});

applyMode("all");
loadStatus();
setInterval(loadStatus, 5000);
