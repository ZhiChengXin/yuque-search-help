const $ = (id) => document.getElementById(id);

const MODE_META = {
  all: {
    hint: "直接描述问题，例如「特定邮件打开导致网页崩溃」",
    suggestTitle: "试试这些问题",
    suggestSub: "根据知识库随机推荐，点击即可查询",
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
    hint: "输入故障现象或 FAQ 标题，结果优先问题排查类文档",
    suggestTitle: "常见故障",
    suggestSub: "从问题排查文档中随机推荐",
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
    hint: "输入操作步骤关键词，例如「绑定邮箱」「签名管理」「定时发送」",
    suggestTitle: "操作指引",
    suggestSub: "从操作手册中随机推荐",
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
    suggestTitle: "常用模块",
    suggestSub: "从分工表中随机推荐",
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
  ]);
  const parts = [];

  // 整句（去掉问答前缀后）优先，便于标题整句点亮
  if (q.length >= 2) parts.push(q);

  // 按标点切开
  q.split(/[\s，。？！、；：,.!?;:（）()【】\[\]《》"'“”/\\|+]+/)
    .map((p) => p.trim())
    .filter((p) => p.length >= 2)
    .forEach((p) => parts.push(p));

  // 再抽连续中文片段
  const cnChunks = q.match(/[\u4e00-\u9fffA-Za-z0-9]{2,}/g) || [];
  cnChunks.forEach((p) => parts.push(p));

  // 业务高频词额外补强
  ["孚盟邮", "孚盟邮件", "邮箱", "绑定", "同步", "授权码", "千里眼"].forEach((w) => {
    if (q.includes(w)) parts.push(w);
  });

  const seen = new Set();
  const result = [];
  parts
    .filter((p) => p && !stop.has(p))
    .sort((a, b) => b.length - a.length)
    .forEach((p) => {
      const key = p.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      result.push(p);
    });
  return result.slice(0, 12);
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

function paintSuggestions(items) {
  $("suggestGrid").innerHTML = (items || [])
    .map(
      (item, index) => `
      <button type="button" class="suggest-card" data-q="${escapeHtml(item.q)}">
        <span class="suggest-no">${String(index + 1).padStart(2, "0")}</span>
        <strong>${escapeHtml(item.q)}</strong>
        <em>${escapeHtml(item.desc || "点击即可检索")}</em>
      </button>`
    )
    .join("");
}

function fallbackSuggestions(mode) {
  const meta = MODE_META[mode] || MODE_META.all;
  return (meta.suggestions || []).map(([q, desc]) => ({ q, desc }));
}

function renderSuggestions(mode, items) {
  const meta = MODE_META[mode] || MODE_META.all;
  $("suggestTitle").textContent = meta.suggestTitle || "试试这些问题";
  $("suggestSub").textContent = meta.suggestSub || "点击即可检索";
  paintSuggestions(items && items.length ? items : fallbackSuggestions(mode));
}

async function loadSuggestions(mode) {
  const target = mode || currentMode;
  renderSuggestions(target, fallbackSuggestions(target));
  try {
    const res = await fetch(
      `/api/suggest?mode=${encodeURIComponent(target)}&limit=6`
    );
    const data = await res.json();
    if (target !== currentMode) return;
    if (data.ok && Array.isArray(data.items) && data.items.length) {
      renderSuggestions(target, data.items);
    }
  } catch {
    // 保持兜底示例
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
  const meta = MODE_META[mode];
  $("searchHint").textContent = meta.hint;
  loadSuggestions(mode);
  if (clearInput) {
    resetSearchPanel();
  }
}

function filterByMode(results) {
  if (currentMode === "all" || currentMode === "owner") return results;
  return results.filter((item) => {
    const path = (item.catalog_path || []).join("/");
    const blob = `${path}/${item.doc_title || ""}`;
    if (currentMode === "faq") return /FAQ|排查|问答|Q&A/i.test(blob);
    if (currentMode === "manual") return /手册|指南|操作/.test(blob);
    return true;
  });
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
        `${data.doc_count || 0} 篇文档 · ${data.section_count || 0} 个章节 · ${data.contact_count || 0} 位联系人`,
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
  box.innerHTML = contacts
    .map(
      (c) => `
      <article class="contact-card">
        <div class="top">
          <h3>${highlight(c.module || c.menu || "模块负责人", query)}</h3>
          <span class="badge">可联系对接</span>
        </div>
        <div class="path">${highlight([c.menu, c.business_line].filter(Boolean).join(" / "), query)}</div>
        <div class="contact-grid">
          <div><span>测试负责人</span><strong>${escapeHtml(c.testers || "暂未登记")}</strong></div>
          <div><span>产品负责人</span><strong>${escapeHtml(c.product_owner || "暂未登记")}</strong></div>
          <div><span>产品联系方式</span><strong>${escapeHtml(c.product_contact || "暂未登记")}</strong></div>
          <div><span>业务线</span><strong>${highlight(c.business_line || "-", query)}</strong></div>
        </div>
      </article>`
    )
    .join("");
}

function renderResults(query, payload) {
  const results = filterByMode(payload.results || []);
  const contacts = payload.contacts || [];
  const confidence = payload.confidence || "none";
  const advice = payload.advice || "";

  const adviceBox = $("adviceBox");
  adviceBox.hidden = !advice;
  adviceBox.className = `advice ${confidence}`;
  adviceBox.textContent = advice;

  $("metaRow").hidden = false;
  $("resultMeta").textContent = `「${query}」· 文档命中 ${results.length} · 联系人 ${contacts.length}`;

  if (currentMode === "owner") {
    renderContacts(contacts, query);
    $("results").innerHTML = "";
    showIntroNote(false);
    if (!contacts.length) {
      setNotice("未匹配到模块负责人", "试试一级/二级菜单名，例如「孚盟邮」「孚盟邮件」「WhatsApp」。");
    } else {
      setNotice("", "");
    }
    return;
  }

  if (!results.length || confidence === "none" || confidence === "low") {
    renderContacts(contacts, query);
  } else {
    renderContacts([]);
  }

  if (!results.length) {
    $("results").innerHTML = "";
    showIntroNote(false);
    setNotice(
      contacts.length ? "文档暂未命中，但已找到可联系的人" : "没有找到足够相关的内容",
      contacts.length
        ? "建议先联系对应测试/产品，或换个关键词再搜。"
        : "可以换模块名、报错原文或更短的关键词再试。"
    );
    return;
  }

  setNotice("", "");
  showIntroNote(false);
  $("results").innerHTML = results
    .map((item, index) => {
      const path = (item.catalog_path || []).join(" / ");
      const openUrl = item.url || item.doc_url;
      const openLabel = item.has_anchor ? "查看该章节" : "打开语雀原文";
      const previewText = item.preview || item.snippet || "";
      const images = Array.isArray(item.images) ? item.images.slice(0, 2) : [];
      const imageHtml = images.length
        ? `<div class="preview-images">${images
            .map(
              (src) => `
            <button type="button" class="preview-image" data-fullsrc="${escapeHtml(src)}" title="点击放大查看">
              <img src="${escapeHtml(src)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
            </button>`
            )
            .join("")}</div>`
        : "";
      const moreHint =
        item.truncated || (item.image_count || 0) > images.length
          ? `<p class="preview-more">内容较多，已折叠展示部分正文${
              (item.image_count || 0) > images.length
                ? `与图片（共 ${item.image_count} 张）`
                : ""
            }，可打开章节查看完整内容。</p>`
          : "";
      return `
        <article class="result-card" style="animation-delay:${index * 40}ms">
          <div class="top">
            <h3><a href="${escapeHtml(openUrl)}" target="_blank" rel="noopener">${highlight(item.title, query)}</a></h3>
            <span class="badge">${escapeHtml(item.match_hint || "相关")}</span>
          </div>
          <div class="doc-title">所属文档：${escapeHtml(item.doc_title || "")}</div>
          ${path ? `<div class="path">${escapeHtml(path)}</div>` : ""}
          <div class="snippet">${highlight(previewText, query)}</div>
          ${imageHtml}
          ${moreHint}
          <div class="card-actions">
            <a class="primary" href="${escapeHtml(openUrl)}" target="_blank" rel="noopener">${openLabel}</a>
          </div>
        </article>`;
    })
    .join("");
}

async function doSearch(query) {
  const q = (query || "").trim();
  if (!q) return;
  $("q").value = q;
  $("searchBtn").disabled = true;
  $("searchBtn").innerHTML = "<span>检索中</span>";
  showIntroNote(false);
  setNotice("", "");
  $("results").innerHTML = `<div class="empty"><p>正在检索…</p></div>`;
  $("contacts").hidden = true;

  try {
    const res = await fetch(
      `/api/search?q=${encodeURIComponent(q)}&limit=12&mode=${encodeURIComponent(currentMode)}`
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
  } catch {
    $("adviceBox").hidden = false;
    $("adviceBox").className = "advice none";
    $("adviceBox").textContent = "搜索请求失败，请确认服务已启动";
    $("results").innerHTML = "";
    setNotice("搜索请求失败", "请确认服务已启动后重试");
  } finally {
    $("searchBtn").disabled = false;
    $("searchBtn").innerHTML = "<span>立即检索</span>";
  }
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
  doSearch(btn.dataset.q);
});

let lightboxScale = 1;
let lightboxOffset = { x: 0, y: 0 };
let lightboxDragging = false;
let lightboxDragStart = { x: 0, y: 0 };

function applyLightboxTransform() {
  const img = $("lightboxImg");
  img.style.transform = `translate(${lightboxOffset.x}px, ${lightboxOffset.y}px) scale(${lightboxScale})`;
  $("lightboxZoomLabel").textContent = `${Math.round(lightboxScale * 100)}%`;
}

function setLightboxZoom(next) {
  lightboxScale = Math.min(4, Math.max(0.5, next));
  if (lightboxScale <= 1) {
    lightboxOffset = { x: 0, y: 0 };
  }
  applyLightboxTransform();
}

function openLightbox(src) {
  if (!src) return;
  lightboxScale = 1;
  lightboxOffset = { x: 0, y: 0 };
  $("lightboxImg").src = src;
  applyLightboxTransform();
  $("lightbox").hidden = false;
  document.body.style.overflow = "hidden";
}

function closeLightbox() {
  $("lightbox").hidden = true;
  $("lightboxImg").removeAttribute("src");
  lightboxScale = 1;
  lightboxOffset = { x: 0, y: 0 };
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
  setLightboxZoom(lightboxScale + 0.25);
});
$("lightboxZoomOut").addEventListener("click", (e) => {
  e.stopPropagation();
  setLightboxZoom(lightboxScale - 0.25);
});
$("lightboxZoomReset").addEventListener("click", (e) => {
  e.stopPropagation();
  setLightboxZoom(1);
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
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.15 : 0.15;
    setLightboxZoom(lightboxScale + delta);
  },
  { passive: false }
);

$("lightboxImg").addEventListener("pointerdown", (e) => {
  if (lightboxScale <= 1) return;
  lightboxDragging = true;
  lightboxDragStart = {
    x: e.clientX - lightboxOffset.x,
    y: e.clientY - lightboxOffset.y,
  };
  $("lightboxImg").classList.add("is-dragging");
  $("lightboxImg").setPointerCapture(e.pointerId);
});

$("lightboxImg").addEventListener("pointermove", (e) => {
  if (!lightboxDragging) return;
  lightboxOffset = {
    x: e.clientX - lightboxDragStart.x,
    y: e.clientY - lightboxDragStart.y,
  };
  applyLightboxTransform();
});

$("lightboxImg").addEventListener("pointerup", () => {
  lightboxDragging = false;
  $("lightboxImg").classList.remove("is-dragging");
});

document.addEventListener("keydown", (e) => {
  if ($("lightbox").hidden) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "+" || e.key === "=") setLightboxZoom(lightboxScale + 0.25);
  if (e.key === "-") setLightboxZoom(lightboxScale - 0.25);
  if (e.key === "0") setLightboxZoom(1);
});

$("syncBtn").addEventListener("click", async () => {
  $("syncBtn").disabled = true;
  setStatus("同步中", "正在刷新知识库…", "loading");
  try {
    await fetch("/api/sync", { method: "POST" });
  } finally {
    await loadStatus();
    $("syncBtn").disabled = false;
  }
});

applyMode("all");
loadStatus();
setInterval(loadStatus, 5000);
