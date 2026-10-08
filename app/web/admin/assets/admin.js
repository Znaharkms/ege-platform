"use strict";

const API = "/api/v1";
const state = {
  token: sessionStorage.getItem("egeAdminToken"),
  user: null,
  contentEtag: null,
  contentStatus: null,
  questionEtag: null,
  questionStatus: null,
  testEtag: null,
  testStatus: null,
  contentSources: new Map(),
  activeReference: null,
  contentCursor: null,
  contentHasMore: false,
  contentLoading: false,
  referenceViewMode: localStorage.getItem("egeReferenceViewMode") === "list" ? "list" : "cards",
  customEntryArchived: false,
  activeTest: null,
  activeTestQuestions: [],
  questionImage: null,
  questionBankOpen: false,
  questionBankCursor: null,
  historyTrainerOpen: false,
  historyTrainerCursor: null,
  errorReportsCursor: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 4200);
}

function setBusy(button, busy) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = "Подождите…";
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});

  const isFormData = options.body instanceof FormData;
  if (options.body !== undefined && !isFormData) headers.set("Content-Type", options.contentType || "application/json");
  const response = await window.accountRequest(path, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : isFormData ? options.body : JSON.stringify(options.body),
    credentials: "same-origin",
  });

  let data = null;
  if (response.status !== 204) {
    const text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch { data = { detail: text }; }
    }
  }
  if (!response.ok) {
    const error = new Error(data?.detail || data?.title || `Ошибка HTTP ${response.status}`);
    error.status = response.status;
    error.code = data?.code;
    error.data = data;
    throw error;
  }
  return { data, response };
}

function logout(message = "") {
  state.token = null;
  state.user = null;
  sessionStorage.removeItem("egeAdminToken");
  $("#app-screen").classList.add("hidden");
  $("#login-screen").classList.remove("hidden");
  if (message) showToast(message, true);
}

async function restoreSession() {
  if (!await window.accountIdentity()) return;
  try {
    const { data } = await api("/me");
    if (data.role !== "admin") throw new Error("У пользователя нет роли администратора");
    state.user = data;
    enterApp();
  } catch (error) {
    logout(error.message);
  }
}

function enterApp() {
  $("#login-screen").classList.add("hidden");
  $("#app-screen").classList.remove("hidden");
  $("#admin-identity").innerHTML = `<strong>${escapeHtml(state.user?.displayName || "Администратор")}</strong><br>${escapeHtml(state.user?.email || "")}`;
  loadContentWorkspace();
  loadErrorReports(true);
}

$("#email-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true);
  try {
    const { data: delivery } = await api("/auth/email/request-code", {
      method: "POST",
      body: { email: $("#login-email").value.trim() },
    });
    $("#email-form").classList.add("hidden");
    $("#code-form").classList.remove("hidden");
    $("#login-code").focus();
    $("#login-hint").textContent = delivery.deliveryMode === "email" ? "Код отправлен на вашу почту. Действует 10 минут. Новый можно запросить через 60 секунд." : "Код создан. Посмотрите его в терминале Uvicorn и введите сюда.";
    showToast("Код входа создан");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(button, false);
  }
});

$("#code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true);
  try {
    const { data } = await api("/auth/email/verify-code", {
      method: "POST",
      body: {
        email: $("#login-email").value.trim(),
        code: $("#login-code").value.trim(),
      },
    });
    window.acceptAccount(data);
    if (data.user.role !== "admin") throw new Error("Эта учётная запись не является администратором");
    state.token = data.accessToken;
    state.user = data.user;
    sessionStorage.setItem("egeAdminToken", state.token);
    enterApp();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(button, false);
  }
});

$("#resend-code").addEventListener("click",()=>{$("#login-code").value='';$("#email-form").requestSubmit();});
$("#change-email").addEventListener("click", () => {
  $("#code-form").classList.add("hidden");
  $("#email-form").classList.remove("hidden");
  $("#login-hint").textContent = "Укажите почту аккаунта, на неё придёт код входа.";
});

$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* local logout still works */ }
  window.clearAccount();
  logout();
});

$$('.nav-item').forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.view !== "history-trainer") state.historyTrainerOpen = false;
  $$('.nav-item').forEach((item) => item.classList.toggle("active", item === button));
  $$('.view').forEach((view) => view.classList.add("hidden"));
  $(`#${button.dataset.view}-view`).classList.remove("hidden");
  $(".sidebar").classList.remove("open");
  if (button.dataset.view === "tests") showTestsPicker();
  else if (button.dataset.view === "content") showReferencePicker();
  else if (button.dataset.view === "history-trainer") openHistoryTrainer();
  else if (button.dataset.view === "error-reports") loadErrorReports(true);
  else if (button.dataset.view === "students") loadStudents(true);
  else if (button.dataset.view === "audit") loadAdminJournal(true);
  else if (button.dataset.view === "promotions") window.openPromotionAdmin();
  else if (button.dataset.view === "search-statistics") loadSearchStatistics();
}));

$("#mobile-nav-toggle").addEventListener("click", () => $(".sidebar").classList.toggle("open"));

function statusLabel(status) {
  return { draft: "Черновик", published: "Опубликован", archived: "Архив" }[status] || status;
}

function subjectLabel(subject) {
  return subject === "history" ? "История" : "Обществознание";
}

function typeLabel(type) {
  return {
    single_choice: "Один ответ",
    multiple_choice: "Несколько ответов",
    text: "Текст",
    matching: "Сопоставление",
    ordering: "Последовательность",
    self_check: "Развёрнутый ответ",
    map: "Карта",
    graph: "График",
  }[type] || type;
}

function slugify(value) {
  const translit = { а:"a",б:"b",в:"v",г:"g",д:"d",е:"e",ё:"e",ж:"zh",з:"z",и:"i",й:"i",к:"k",л:"l",м:"m",н:"n",о:"o",п:"p",р:"r",с:"s",т:"t",у:"u",ф:"f",х:"h",ц:"c",ч:"ch",ш:"sh",щ:"sch",ъ:"",ы:"y",ь:"",э:"e",ю:"yu",я:"ya" };
  return value.toLowerCase().split("").map((char) => translit[char] ?? char).join("")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
}

function renderEmpty(container, title, text) {
  container.innerHTML = `<div class="empty-state"><h3>${escapeHtml(title)}</h3><p class="muted">${escapeHtml(text)}</p></div>`;
}

function contentTypeLabel(type) {
  return { date: "Дата", term: "Термин", plan: "План" }[type] || type;
}

const referenceMeta = {
  history_dates: { icon: "▣" },
  history_terms: { icon: "▤" },
  society_terms: { icon: "◫" },
  society_plans: { icon: "☷" },
};

function referenceCard(book) {
  const icon = referenceMeta[book.code]?.icon || "▦";
  return `
    <button class="source-card" type="button" data-book-code="${escapeHtml(book.code)}"
      aria-label="${escapeHtml(book.title)}. Открыть двойным кликом"
      title="Двойной клик — открыть">
      <span class="source-card-top"><span class="source-card-icon" aria-hidden="true">${icon}</span>
        <span class="source-count">${book.recordCount.toLocaleString("ru-RU")}</span></span>
      <strong>${escapeHtml(book.title)}</strong>
    </button>`;
}

function archivedReferenceCard(book) {
  return `
    <article class="source-card archived">
      <span class="source-card-top"><span class="source-card-icon" aria-hidden="true">▦</span>
        <span class="source-count">${book.recordCount.toLocaleString("ru-RU")}</span></span>
      <strong>${escapeHtml(book.title)}</strong>
      <div class="source-card-actions"><button class="button compact secondary restore-reference"
        data-book-id="${book.id}" type="button">Восстановить</button></div>
    </article>`;
}

function bindReferenceCards(container) {
  $$("[data-book-code]", container).forEach((button) => {
    button.addEventListener("dblclick", () => openReference(button.dataset.bookCode));
    button.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openReference(button.dataset.bookCode);
      }
    });
  });
  $$(".restore-reference", container).forEach((button) => button.addEventListener("click", async () => {
    if (!confirm("Восстановить этот справочник вместе со всеми данными?")) return;
    try {
      await api(`/admin/reference-books/${button.dataset.bookId}/restore`, { method: "POST" });
      showToast("Справочник восстановлен");
      await loadContentSources();
    } catch (error) { showToast(error.message, true); }
  }));
}

async function loadContentSources() {
  const container = $("#source-cards");
  applyReferenceViewMode(state.referenceViewMode);
  container.innerHTML = '<div class="loading">Загружаем справочники…</div>';
  try {
    const { data } = await api("/admin/reference-books");
    state.contentSources = new Map(data.items.map((book) => [book.code, book]));
    const active = data.items.filter((book) => book.status === "active");
    const history = active.filter((book) => book.subject === "history");
    const society = active.filter((book) => book.subject === "society");
    const archived = data.items.filter((book) => book.status === "archived");
    container.innerHTML = `
      <section class="reference-group history">
        <div class="reference-group-heading"><span class="reference-group-icon" aria-hidden="true">⌂</span><h2>История</h2></div>
        <div class="reference-grid">${history.map(referenceCard).join("") || '<p class="muted">Нет активных справочников</p>'}</div>
      </section>
      <section class="reference-group society">
        <div class="reference-group-heading"><span class="reference-group-icon" aria-hidden="true">§</span><h2>Обществознание</h2></div>
        <div class="reference-grid">${society.map(referenceCard).join("") || '<p class="muted">Нет активных справочников</p>'}</div>
      </section>
      ${archived.length ? `<section class="reference-group archived">
        <div class="reference-group-heading"><span class="reference-group-icon" aria-hidden="true">↶</span><h2>Архив справочников</h2></div>
        <div class="reference-grid">${archived.map(archivedReferenceCard).join("")}</div>
      </section>` : ""}`;
    bindReferenceCards(container);
  } catch (error) {
    container.innerHTML = `<div class="empty-state"><h3>Не удалось загрузить справочники</h3><p class="muted">${escapeHtml(error.message)}</p></div>`;
  }
}

function referenceUsesSectionFilter(code) {
  return ["history_dates", "history_terms", "society_terms", "society_plans"].includes(code);
}

async function fillReferenceSectionFilter() {
  const select = $("#content-period-filter");
  const isHistory = state.activeReference?.subject === "history";
  select.innerHTML = `<option value="">${isHistory ? "Все периоды" : "Все разделы"}</option>`;
  select.setAttribute("aria-label", isHistory ? "Период" : "Раздел");
  if (!referenceUsesSectionFilter(state.activeReference?.code)) return;
  try {
    const { data } = await api(`/subjects/${state.activeReference.subject}/sections`);
    for (const item of flattenSections(data.items)) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.title;
      select.append(option);
    }
  } catch (error) { showToast(`Периоды не загружены: ${error.message}`, true); }
}

async function openReference(code) {
  const book = state.contentSources.get(code);
  if (!book || book.status !== "active") return;
  state.activeReference = book;
  $("#content-search").value = "";
  $("#content-status-filter").innerHTML = book.kind === "custom"
    ? '<option value="">Активные записи</option><option value="archived">Архив записей</option>'
    : '<option value="">Все статусы</option><option value="draft">Черновики</option><option value="published">Опубликованные</option><option value="archived">Архив</option>';
  $("#active-reference-subject").textContent = subjectLabel(book.subject);
  $("#active-reference-title").textContent = book.title;
  $("#reference-settings-button").classList.toggle("hidden", book.kind !== "custom");
  $("#content-period-filter").classList.toggle("hidden", !referenceUsesSectionFilter(book.code));
  $("#content-picker").classList.add("hidden");
  $("#content-workspace").classList.remove("hidden");
  await fillReferenceSectionFilter();
  await loadContent(true);
}

async function showReferencePicker() {
  state.activeReference = null;
  $("#content-workspace").classList.add("hidden");
  $("#content-picker").classList.remove("hidden");
  await loadContentSources();
}

function builtInContentCard(item) {
  const code = state.activeReference.code;
  const descriptionClass = code === "society_terms" ? "card-description two-lines" : "card-description";
  const description = item.summary ? `<p class="${descriptionClass}">${escapeHtml(item.summary)}</p>` : "";
  const meta = [];
  if (item.section) meta.push(`<span>${escapeHtml(item.section.title)}</span>`);
  return `
    <article class="content-card editable-content" data-id="${item.id}" tabindex="0">
      <div><span class="badge ${item.status}">${statusLabel(item.status)}</span>
        <h3 class="card-title">${escapeHtml(item.title)}</h3>
        ${(["history_dates", "history_terms", "society_terms"].includes(code)) ? description : ""}
        <div class="card-meta">${meta.join("")}</div></div>
      <div class="card-actions"><button class="button secondary edit-content" data-id="${item.id}" type="button">Открыть</button></div>
    </article>`;
}

function customContentCard(item) {
  const fields = state.activeReference.fields;
  const first = fields[0];
  const title = first ? item.values[first.key] : "Запись";
  const details = fields.slice(1).map((field) => {
    const value = item.values[field.key];
    if (value === undefined || value === null || value === "") return "";
    return `<span><strong>${escapeHtml(field.label)}:</strong> ${escapeHtml(String(value))}</span>`;
  }).filter(Boolean).join("");
  return `
    <article class="content-card editable-custom-entry" data-id="${item.id}" tabindex="0">
      <div>${item.archived ? '<span class="badge archived">Архив</span>' : ""}
        <h3 class="card-title">${escapeHtml(String(title || "Без названия"))}</h3>
        <div class="card-meta">${details}</div></div>
      <div class="card-actions"><button class="button secondary edit-custom-entry" data-id="${item.id}" type="button">Открыть</button></div>
    </article>`;
}

function bindContentCards(container) {
  $$(".edit-content", container).forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "true";
    button.addEventListener("click", () => editContent(button.dataset.id));
  });
  $$(".editable-content", container).forEach((card) => {
    if (card.dataset.bound) return;
    card.dataset.bound = "true";
    card.addEventListener("dblclick", (event) => {
      if (!event.target.closest("button")) editContent(card.dataset.id);
    });
  });
  $$(".edit-custom-entry", container).forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "true";
    button.addEventListener("click", () => editCustomEntry(button.dataset.id));
  });
  $$(".editable-custom-entry", container).forEach((card) => {
    if (card.dataset.bound) return;
    card.dataset.bound = "true";
    card.addEventListener("dblclick", (event) => {
      if (!event.target.closest("button")) editCustomEntry(card.dataset.id);
    });
  });
}

async function loadContent(reset = true) {
  if (!state.activeReference || state.contentLoading) return;
  const list = $("#content-list");
  if (reset) {
    state.contentCursor = null;
    state.contentHasMore = false;
    list.innerHTML = '<div class="loading">Загружаем справочник…</div>';
  }
  state.contentLoading = true;
  const sentinel = $("#content-load-sentinel");
  sentinel.classList.remove("hidden");
  const params = new URLSearchParams({ limit: "50" });
  if (state.contentCursor) params.set("cursor", state.contentCursor);
  const status = $("#content-status-filter").value;
  const query = $("#content-search").value.trim();
  if (query) params.set("q", query);
  let path;
  if (state.activeReference.kind === "custom") {
    if (status === "archived") params.set("archived", "true");
    path = `/admin/reference-books/${state.activeReference.id}/entries?${params}`;
  } else {
    params.set("subject", state.activeReference.subject);
    params.append("type", state.activeReference.contentType);
    if (status) params.set("status", status);
    const section = $("#content-period-filter").value;
    if (referenceUsesSectionFilter(state.activeReference.code) && section) params.set("sectionId", section);
    path = `/admin/content?${params}`;
  }
  try {
    const { data } = await api(path);
    if (reset) list.innerHTML = "";
    if (!data.items.length && reset) {
      renderEmpty(list, "Материалы не найдены", "В этом справочнике пока нет подходящих записей.");
    } else {
      const html = data.items.map((item) => state.activeReference.kind === "custom"
        ? customContentCard(item) : builtInContentCard(item)).join("");
      list.insertAdjacentHTML("beforeend", html);
      bindContentCards(list);
    }
    state.contentCursor = data.page.nextCursor;
    state.contentHasMore = data.page.hasMore;
  } catch (error) {
    if (error.status === 401) return logout("Сессия закончилась. Войдите снова.");
    if (reset) renderEmpty(list, "Не удалось загрузить справочник", error.message);
    else showToast(error.message, true);
  } finally {
    state.contentLoading = false;
    sentinel.classList.toggle("hidden", !state.contentHasMore);
    if (state.contentHasMore) requestAnimationFrame(() => {
      if (sentinel.getBoundingClientRect().top < window.innerHeight + 250) loadContent(false);
    });
  }
}

async function loadContentWorkspace() {
  if (state.activeReference) await loadContent(true);
  else await loadContentSources();
}

$("#refresh-content").addEventListener("click", () => loadContent(true));
$("#back-to-references").addEventListener("click", showReferencePicker);
$("#content-status-filter").addEventListener("change", () => loadContent(true));
$("#content-period-filter").addEventListener("change", () => loadContent(true));
function applyReferenceViewMode(mode) {
  state.referenceViewMode = mode === "list" ? "list" : "cards";
  localStorage.setItem("egeReferenceViewMode", state.referenceViewMode);
  $("#source-cards").classList.toggle("list-view", state.referenceViewMode === "list");
  for (const [buttonId, buttonMode] of [["#reference-view-list", "list"], ["#reference-view-cards", "cards"]]) {
    const button = $(buttonId);
    const active = state.referenceViewMode === buttonMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

$("#reference-view-list").addEventListener("click", () => applyReferenceViewMode("list"));
$("#reference-view-cards").addEventListener("click", () => applyReferenceViewMode("cards"));
$("#content-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); loadContent(true); }
});

new IntersectionObserver((entries) => {
  if (entries.some((entry) => entry.isIntersecting) && state.contentHasMore) loadContent(false);
}, { rootMargin: "250px" }).observe($("#content-load-sentinel"));

function showContentDetailsEditor() {
  const type = $("#content-type").value;
  $$(".content-details-editor").forEach((editor) => editor.classList.add("hidden"));
  const editor = $(`#${type}-details-editor`);
  if (editor) editor.classList.remove("hidden");
  if (state.activeReference?.code === "history_dates") $("#content-date-event-field").classList.add("hidden");
  if (state.activeReference?.code === "history_terms") $("#term-details-editor").classList.add("hidden");
}

$("#content-type").addEventListener("change", showContentDetailsEditor);

function configureBuiltInEditor() {
  const code = state.activeReference?.code;
  const historyTerm = code === "history_terms";
  const societyTerm = code === "society_terms";
  const societyPlan = code === "society_plans";
  $("#content-system-fields").classList.toggle("hidden", historyTerm);
  $("#content-subject-field").classList.add("hidden");
  $("#content-type-field").classList.add("hidden");
  $("#content-slug-field").classList.add("hidden");
  $("#content-section-field").classList.toggle("hidden", historyTerm);
  $("#content-section-label").textContent = code === "history_dates" ? "Период" : "Раздел";
  $("#content-title-label").textContent = code === "history_dates" ? "Дата" : (historyTerm || societyTerm) ? "Термин" : "Название";
  $("#content-summary-label").textContent = code === "history_dates" ? "Событие" : code === "history_terms" ? "Определение" : "Краткое описание";
  $("#content-summary-field").classList.toggle("hidden", societyTerm || societyPlan);
  $("#content-aliases-field").classList.toggle("hidden", code === "history_dates" || societyTerm || societyPlan);
  $("#content-definition-field").classList.toggle("hidden", !societyTerm);
  $("#content-features-field").classList.toggle("hidden", societyTerm);
  $("#content-date-event-field").classList.toggle("hidden", code === "history_dates");
  showContentDetailsEditor();
}

function resetContentForm() {
  $("#content-form").reset();
  $("#content-id").value = "";
  state.contentEtag = null;
  state.contentStatus = null;
  $("#publish-content").classList.add("hidden");
  $("#archive-content").classList.add("hidden");
  configureBuiltInEditor();
}

$("#new-content-button").addEventListener("click", async () => {
  if (state.activeReference?.kind === "custom") return openCustomEntry();
  resetContentForm();
  $("#content-subject").value = state.activeReference.subject;
  $("#content-type").value = state.activeReference.contentType;
  configureBuiltInEditor();
  $("#content-drawer-title").textContent = "Новый материал";
  await fillSections($("#content-subject").value, $("#content-section"));
  if (state.activeReference.code === "society_plans") populatePlanBuilder([]);
  openDrawer($("#content-drawer"));
});

function addReferenceFieldRow(field = {}) {
  const row = document.createElement("div");
  row.className = "reference-field-row";
  row.dataset.id = field.id || "";
  row.innerHTML = `
    <label>Название поля<input class="reference-field-label" type="text" maxlength="200" required value="${escapeHtml(field.label || "")}" placeholder="Например, Автор"></label>
    <label>Тип<select class="reference-field-type">
      <option value="text">Короткий текст</option><option value="long_text">Большой текст</option>
      <option value="number">Число</option><option value="date">Дата</option><option value="boolean">Да / нет</option>
    </select></label>
    <label class="reference-required"><input class="reference-field-required" type="checkbox"> Обязательное</label>
    <button class="remove-row remove-reference-field" type="button" aria-label="Удалить поле">×</button>`;
  $(".reference-field-type", row).value = field.type || "text";
  $(".reference-field-required", row).checked = Boolean(field.required);
  $(".remove-reference-field", row).addEventListener("click", () => {
    const message = row.dataset.id
      ? "Удалить поле? Значения этого поля будут удалены из всех записей и восстановить их будет нельзя."
      : "Удалить это поле?";
    if (!confirm(message)) return;
    if ($$(".reference-field-row", $("#reference-fields")).length <= 1) {
      showToast("В справочнике должно остаться хотя бы одно поле", true);
      return;
    }
    row.remove();
  });
  $("#reference-fields").append(row);
}

function resetReferenceForm(book = null) {
  $("#reference-form").reset();
  $("#reference-fields").innerHTML = "";
  $("#reference-id").value = book?.id || "";
  $("#reference-subject").value = book?.subject || "history";
  $("#reference-title").value = book?.title || "";
  $("#reference-subject-field").classList.toggle("hidden", Boolean(book));
  $("#reference-drawer-title").textContent = book ? "Настройки справочника" : "Новый справочник";
  const fields = book?.fields?.length ? book.fields : [{ label: "Название", type: "text", required: true }];
  fields.forEach(addReferenceFieldRow);
}

$("#create-reference-button").addEventListener("click", () => {
  resetReferenceForm();
  openDrawer($("#reference-drawer"));
});
$("#reference-settings-button").addEventListener("click", () => {
  if (!state.activeReference || state.activeReference.kind !== "custom") return;
  resetReferenceForm(state.activeReference);
  openDrawer($("#reference-drawer"));
});
$("#add-reference-field").addEventListener("click", () => addReferenceFieldRow());

$("#reference-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  const fields = $$(".reference-field-row", $("#reference-fields")).map((row) => ({
    ...(row.dataset.id ? { id: row.dataset.id } : {}),
    label: $(".reference-field-label", row).value.trim(),
    type: $(".reference-field-type", row).value,
    required: $(".reference-field-required", row).checked,
  }));
  if (!fields.length) return showToast("Добавьте хотя бы одно поле", true);
  setBusy(button, true);
  try {
    const id = $("#reference-id").value;
    const payload = { title: $("#reference-title").value.trim(), fields };
    const { data } = id
      ? await api(`/admin/reference-books/${id}`, { method: "PATCH", contentType: "application/merge-patch+json", body: payload })
      : await api("/admin/reference-books", { method: "POST", body: { subject: $("#reference-subject").value, ...payload } });
    showToast(id ? "Настройки справочника сохранены" : "Справочник создан");
    closeDrawers();
    await loadContentSources();
    if (id) await openReference(data.code);
  } catch (error) { showToast(error.message, true); }
  finally { setBusy(button, false); }
});

$("#archive-reference-button").addEventListener("click", async () => {
  const book = state.activeReference;
  if (!book) return;
  if (!confirm(`Архивировать справочник «${book.title}»? Все данные сохранятся, и справочник можно будет восстановить.`)) return;
  try {
    await api(`/admin/reference-books/${book.id}/archive`, { method: "POST" });
    showToast("Справочник перемещён в архив");
    await showReferencePicker();
  } catch (error) { showToast(error.message, true); }
});

function customFieldInput(field, value = null) {
  const id = `custom-field-${field.id}`;
  const required = field.required ? " required" : "";
  if (field.type === "long_text") {
    return `<label for="${id}">${escapeHtml(field.label)}<textarea id="${id}" data-key="${field.key}" data-type="${field.type}" rows="5"${required}>${escapeHtml(value ?? "")}</textarea></label>`;
  }
  if (field.type === "boolean") {
    return `<label class="check-row"><input id="${id}" data-key="${field.key}" data-type="${field.type}" type="checkbox" ${value ? "checked" : ""}> ${escapeHtml(field.label)}</label>`;
  }
  const inputType = field.type === "number" ? "number" : field.type === "date" ? "date" : "text";
  return `<label for="${id}">${escapeHtml(field.label)}<input id="${id}" data-key="${field.key}" data-type="${field.type}" type="${inputType}" value="${escapeHtml(value ?? "")}"${required}></label>`;
}

function openCustomEntry(entry = null) {
  $("#custom-entry-form").reset();
  $("#custom-entry-id").value = entry?.id || "";
  $("#custom-entry-drawer-title").textContent = entry ? "Редактирование записи" : "Новая запись";
  $("#custom-entry-fields").innerHTML = state.activeReference.fields
    .map((field) => customFieldInput(field, entry?.values?.[field.key])).join("");
  state.customEntryArchived = Boolean(entry?.archived);
  $("#archive-custom-entry").classList.toggle("hidden", !entry || entry.archived);
  $("#restore-custom-entry").classList.toggle("hidden", !entry?.archived);
  openDrawer($("#custom-entry-drawer"));
}

async function editCustomEntry(id) {
  try {
    const { data } = await api(`/admin/reference-books/${state.activeReference.id}/entries/${id}`);
    openCustomEntry(data);
  } catch (error) { showToast(error.message, true); }
}

function customEntryValues() {
  const values = {};
  $$('[data-key]', $("#custom-entry-fields")).forEach((input) => {
    let value = input.type === "checkbox" ? input.checked : input.value.trim();
    if (input.dataset.type === "number" && value !== "") value = Number(value);
    values[input.dataset.key] = value;
  });
  return values;
}

$("#custom-entry-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true);
  try {
    const id = $("#custom-entry-id").value;
    const path = `/admin/reference-books/${state.activeReference.id}/entries${id ? `/${id}` : ""}`;
    await api(path, { method: id ? "PATCH" : "POST", body: { values: customEntryValues() } });
    showToast("Запись сохранена");
    closeDrawers();
    await loadContent(true);
  } catch (error) { showToast(error.message, true); }
  finally { setBusy(button, false); }
});

async function changeCustomEntryArchive(restore) {
  const id = $("#custom-entry-id").value;
  if (!id) return;
  const verb = restore ? "Восстановить" : "Архивировать";
  if (!confirm(`${verb} эту запись?`)) return;
  try {
    await api(`/admin/reference-books/${state.activeReference.id}/entries/${id}/${restore ? "restore" : "archive"}`, { method: "POST" });
    showToast(restore ? "Запись восстановлена" : "Запись перемещена в архив");
    closeDrawers();
    await loadContent(true);
  } catch (error) { showToast(error.message, true); }
}
$("#archive-custom-entry").addEventListener("click", () => changeCustomEntryArchive(false));
$("#restore-custom-entry").addEventListener("click", () => changeCustomEntryArchive(true));

let draggedPlanNode = null;

function renumberPlanBuilder() {
  $$(".plan-point", $("#plan-items-builder")).forEach((point, index) => {
    $(".plan-number", point).textContent = `${index + 1}.`;
  });
}

function addPlanSubpoint(container, item = {}) {
  const row = document.createElement("div");
  row.className = "plan-subpoint";
  row.innerHTML = `
    <button class="plan-drag-handle" type="button" draggable="true" aria-label="Перетащить подпункт">⋮⋮</button>
    <span class="plan-bullet" aria-hidden="true">•</span>
    <input class="plan-item-input plan-subpoint-input" type="text" maxlength="2000" value="${escapeHtml(item.text || "")}" placeholder="Текст подпункта" aria-label="Подпункт плана">
    <button class="remove-row remove-plan-subpoint" type="button" aria-label="Удалить подпункт">×</button>`;
  $(".remove-plan-subpoint", row).addEventListener("click", () => row.remove());
  bindPlanDragHandle($(".plan-drag-handle", row), row);
  container.append(row);
  return row;
}

function addPlanPoint(item = {}) {
  const point = document.createElement("div");
  point.className = "plan-point";
  point.innerHTML = `
    <div class="plan-point-row">
      <button class="plan-drag-handle" type="button" draggable="true" aria-label="Перетащить пункт">⋮⋮</button>
      <span class="plan-number"></span>
      <input class="plan-item-input plan-point-input" type="text" maxlength="2000" value="${escapeHtml(item.text || "")}" placeholder="Текст пункта" aria-label="Пункт плана">
      <button class="button secondary compact add-plan-subpoint" type="button">+ Подпункт</button>
      <button class="remove-row remove-plan-point" type="button" aria-label="Удалить пункт">×</button>
    </div>
    <div class="plan-subpoints"></div>`;
  const subpoints = $(".plan-subpoints", point);
  $(".add-plan-subpoint", point).addEventListener("click", () => {
    const row = addPlanSubpoint(subpoints);
    $(".plan-subpoint-input", row).focus();
  });
  $(".remove-plan-point", point).addEventListener("click", () => {
    point.remove();
    renumberPlanBuilder();
  });
  bindPlanDragHandle($(".plan-point-row .plan-drag-handle", point), point);
  for (const child of item.children || []) addPlanSubpoint(subpoints, child);
  $("#plan-items-builder").append(point);
  renumberPlanBuilder();
  return point;
}

function bindPlanDragHandle(handle, node) {
  handle.addEventListener("dragstart", (event) => {
    draggedPlanNode = node;
    node.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "plan-item");
  });
  handle.addEventListener("dragend", () => {
    node.classList.remove("dragging");
    draggedPlanNode = null;
    renumberPlanBuilder();
  });
}

function reorderDraggedPlanNode(container, event, selector) {
  if (!draggedPlanNode?.matches(selector)) return;
  event.preventDefault();
  const candidates = [...container.querySelectorAll(`:scope > ${selector}`)]
    .filter((node) => node !== draggedPlanNode);
  const after = candidates.find((node) => {
    const box = node.getBoundingClientRect();
    return event.clientY < box.top + box.height / 2;
  });
  container.insertBefore(draggedPlanNode, after || null);
  renumberPlanBuilder();
}

$("#plan-items-builder").addEventListener("dragover", (event) => {
  reorderDraggedPlanNode($("#plan-items-builder"), event, ".plan-point");
});
$("#plan-items-builder").addEventListener("dragover", (event) => {
  if (!draggedPlanNode?.classList.contains("plan-subpoint")) return;
  const subpoints = event.target.closest(".plan-subpoints");
  if (subpoints) reorderDraggedPlanNode(subpoints, event, ".plan-subpoint");
});
$("#plan-items-builder").addEventListener("drop", (event) => event.preventDefault());
$("#add-plan-point").addEventListener("click", () => {
  const point = addPlanPoint();
  $(".plan-point-input", point).focus();
});

function populatePlanBuilder(items) {
  $("#plan-items-builder").innerHTML = "";
  for (const item of items || []) addPlanPoint(item);
  if (!items?.length) addPlanPoint();
}

function planItemsFromBuilder() {
  return $$(".plan-point", $("#plan-items-builder")).map((point, position) => ({
    text: $(".plan-point-input", point).value.trim(),
    position,
    children: $$(".plan-subpoint", $(".plan-subpoints", point)).map((subpoint, childPosition) => ({
      text: $(".plan-subpoint-input", subpoint).value.trim(),
      position: childPosition,
      children: [],
    })).filter((item) => item.text),
  })).filter((item) => item.text);
}

function populateContentDetails(detail) {
  const details = detail.details || {};
  if (detail.type === "date") {
    $("#content-date-label").value = details.dateLabel || "";
    $("#content-date-precision").value = details.precision || "unknown";
    $("#content-date-start").value = details.dateStart || "";
    $("#content-date-end").value = details.dateEnd || "";
    $("#content-date-event").value = details.eventText || "";
  } else if (detail.type === "term") {
    $("#content-definition").value = details.definition || "";
    $("#content-features").value = (details.features || []).map((item) => item.text).join("\n");
  } else if (detail.type === "plan") {
    $("#content-plan-introduction").value = details.introduction || "";
    populatePlanBuilder(details.items);
    $("#content-plan-conclusion").value = details.conclusion || "";
  }
  showContentDetailsEditor();
}

async function editContent(id) {
  try {
    const { data, response } = await api(`/admin/content/${id}`);
    resetContentForm();
    $("#content-id").value = data.id;
    $("#content-subject").value = data.subject;
    $("#content-subject").disabled = true;
    $("#content-type").value = data.type;
    $("#content-type").disabled = true;
    await fillSections(data.subject, $("#content-section"), data.section?.id || "");
    $("#content-title").value = data.title;
    $("#content-slug").value = data.slug;
    $("#content-summary").value = data.summary || "";
    $("#content-aliases").value = (data.aliases || []).join("\n");
    populateContentDetails(data);
    state.contentEtag = response.headers.get("ETag");
    state.contentStatus = data.status;
    $("#content-drawer-title").textContent = data.title;
    $("#publish-content").classList.toggle("hidden", data.status === "published");
    $("#archive-content").classList.toggle("hidden", data.status === "archived");
    openDrawer($("#content-drawer"));
  } catch (error) {
    showToast(error.message, true);
  }
}

function buildContentDetails(type) {
  if (type === "date") {
    const dateLabel = $("#content-date-label").value.trim();
    const eventText = $("#content-summary").value.trim();
    if (!dateLabel || !eventText) throw new Error("Заполните отображаемую дату и событие");
    return {
      kind: "date",
      dateLabel,
      dateStart: $("#content-date-start").value || null,
      dateEnd: $("#content-date-end").value || null,
      precision: $("#content-date-precision").value,
      eventText,
    };
  }
  if (type === "term") {
    const definition = state.activeReference?.code === "history_terms"
      ? $("#content-summary").value.trim()
      : $("#content-definition").value.trim();
    if (!definition) throw new Error("Заполните определение термина");
    const features = $("#content-features").value.split("\n").map((item) => item.trim()).filter(Boolean)
      .map((textValue, index) => ({ type: "feature", text: textValue, position: index }));
    return { kind: "term", definition, features };
  }
  const items = planItemsFromBuilder();
  if (!items.length) throw new Error("Добавьте хотя бы один пункт плана");
  return {
    kind: "plan",
    introduction: $("#content-plan-introduction").value.trim() || null,
    conclusion: $("#content-plan-conclusion").value.trim() || null,
    items,
  };
}

function isoDate(year, month, day) {
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function romanToNumber(value) {
  const numbers = { I: 1, V: 5, X: 10, L: 50, C: 100 };
  let result = 0;
  let previous = 0;
  for (const char of value.toUpperCase().split("").reverse()) {
    const current = numbers[char] || 0;
    result += current < previous ? -current : current;
    previous = current;
  }
  return result;
}

function parseHistoricalDate(value) {
  const raw = value.trim();
  if (!raw) return null;
  const normalized = raw.toLowerCase().replaceAll("ё", "е").replace(/[–—]/g, "-");
  const months = {
    января: 1, февраля: 2, марта: 3, апреля: 4, мая: 5, июня: 6,
    июля: 7, августа: 8, сентября: 9, октября: 10, ноября: 11, декабря: 12,
  };
  let match = normalized.match(/^(\d{1,2})[.\/]([01]?\d)[.\/](\d{4})(?:\s*г(?:ода|\.)?)?$/);
  if (match) {
    const [, day, month, year] = match;
    const endDay = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
    if (Number(day) <= endDay && Number(month) >= 1 && Number(month) <= 12) {
      const iso = isoDate(Number(year), Number(month), Number(day));
      return { label: raw, precision: "day", start: iso, end: iso };
    }
  }
  match = normalized.match(new RegExp(`^(\\d{1,2})\\s+(${Object.keys(months).join("|")})\\s+(\\d{4})(?:\\s*г(?:ода|\\.)?)?$`));
  if (match) {
    const day = Number(match[1]);
    const month = months[match[2]];
    const year = Number(match[3]);
    const endDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
    if (day <= endDay) {
      const iso = isoDate(year, month, day);
      return { label: raw, precision: "day", start: iso, end: iso };
    }
  }
  match = normalized.match(/^(?:с\s*)?(\d{3,4})\s*(?:-|по)\s*(\d{3,4})(?:\s*гг?\.?)?$/);
  if (match) {
    const startYear = Number(match[1]);
    const endYear = Number(match[2]);
    if (endYear >= startYear) return {
      label: raw, precision: "range", start: isoDate(startYear, 1, 1), end: isoDate(endYear, 12, 31),
    };
  }
  match = normalized.match(/^([ivxlc]+|\d{1,2})\s*век(?:а)?$/i);
  if (match) {
    const century = /^\d+$/.test(match[1]) ? Number(match[1]) : romanToNumber(match[1]);
    if (century > 0) return {
      label: raw, precision: "century", start: isoDate((century - 1) * 100 + 1, 1, 1), end: isoDate(century * 100, 12, 31),
    };
  }
  match = normalized.match(/^(?:около|ок\.?\s*)?(\d{3,4})(?:\s*г(?:од|ода|\.)?)?$/);
  if (match) {
    const year = Number(match[1]);
    return {
      label: raw, precision: /^(около|ок\.)/.test(normalized) ? "approximate" : "year",
      start: isoDate(year, 1, 1), end: isoDate(year, 12, 31),
    };
  }
  return { label: raw, precision: "unknown", start: "", end: "" };
}

function applyParsedHistoricalDate(value) {
  if (state.activeReference?.code !== "history_dates") return;
  const parsed = parseHistoricalDate(value);
  if (!parsed) return;
  $("#content-date-label").value = parsed.label;
  $("#content-date-precision").value = parsed.precision;
  $("#content-date-start").value = parsed.start;
  $("#content-date-end").value = parsed.end;
}

function buildContentPayload() {
  const type = $("#content-type").value;
  return {
    subject: $("#content-subject").value,
    sectionId: $("#content-section").value || null,
    type,
    title: $("#content-title").value.trim(),
    slug: $("#content-slug").value.trim(),
    summary: $("#content-summary").value.trim() || null,
    aliases: $("#content-aliases").value.split("\n").map((item) => item.trim()).filter(Boolean),
    details: buildContentDetails(type),
  };
}

$("#content-title").addEventListener("input", (event) => {
  if (!$("#content-id").value) {
    $("#content-slug").value = slugify(event.target.value) || `material-${crypto.randomUUID().slice(0, 12)}`;
  }
  applyParsedHistoricalDate(event.target.value);
});
$("#content-summary").addEventListener("input", (event) => {
  if (state.activeReference?.code === "history_dates") $("#content-date-event").value = event.target.value;
  if (state.activeReference?.code === "history_terms") $("#content-definition").value = event.target.value;
});

$("#content-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true);
  try {
    const payload = buildContentPayload();
    const id = $("#content-id").value;
    let result;
    if (id) {
      delete payload.subject;
      delete payload.type;
      result = await api(`/admin/content/${id}`, {
        method: "PATCH",
        contentType: "application/merge-patch+json",
        headers: { "If-Match": state.contentEtag },
        body: payload,
      });
    } else {
      result = await api("/admin/content", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: { ...payload, assets: [], relations: [] },
      });
      $("#content-id").value = result.data.id;
      $("#content-subject").disabled = true;
      $("#content-type").disabled = true;
    }
    state.contentEtag = result.response.headers.get("ETag");
    state.contentStatus = result.data.status;
    $("#content-drawer-title").textContent = result.data.title;
    $("#publish-content").classList.toggle("hidden", result.data.status === "published");
    $("#archive-content").classList.toggle("hidden", result.data.status === "archived");
    showToast("Материал сохранён");
    await loadContentWorkspace();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(button, false);
  }
});

async function changeContentStatus(action) {
  const id = $("#content-id").value;
  if (!id) return;
  if (action === "archive" && !confirm("Переместить материал в архив?")) return;
  try {
    await api(`/admin/content/${id}/${action}`, {
      method: "POST",
      headers: { "If-Match": state.contentEtag, "Idempotency-Key": crypto.randomUUID() },
    });
    showToast(action === "publish" ? "Материал опубликован" : "Материал перемещён в архив");
    closeDrawers();
    await loadContentWorkspace();
  } catch (error) {
    showToast(error.message, true);
  }
}

$("#publish-content").addEventListener("click", () => changeContentStatus("publish"));
$("#archive-content").addEventListener("click", () => changeContentStatus("archive"));

function flattenSections(items, depth = 0, result = []) {
  for (const item of items || []) {
    result.push({ ...item, depth });
    flattenSections(item.children, depth + 1, result);
  }
  return result;
}

async function fillSections(subject, select, selected = "") {
  select.innerHTML = '<option value="">Без раздела</option>';
  try {
    const { data } = await api(`/subjects/${subject}/sections`);
    for (const item of flattenSections(data.items)) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${"— ".repeat(item.depth)}${item.title}`;
      option.selected = item.id === selected;
      select.append(option);
    }
  } catch (error) {
    showToast(`Разделы не загружены: ${error.message}`, true);
  }
}

function openDrawer(drawer) {
  $("#drawer-backdrop").classList.remove("hidden");
  drawer.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeDrawers() {
  $("#drawer-backdrop").classList.add("hidden");
  $$(".drawer").forEach((drawer) => drawer.classList.add("hidden"));
  document.body.style.overflow = "";
}

$("#drawer-backdrop").addEventListener("click", closeDrawers);
$$('.close-drawer').forEach((button) => button.addEventListener("click", closeDrawers));

function rowId(prefix = "item") {
  return `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
}

function addChoiceOption(option = {}) {
  const type = $("#question-type").value;
  const row = document.createElement("div");
  row.className = "option-row";
  row.dataset.id = option.id || rowId("option");
  row.innerHTML = `
    <input class="correct-choice" type="${type === "multiple_choice" ? "checkbox" : "radio"}" name="correct-option" ${option.correct ? "checked" : ""} aria-label="Правильный ответ">
    <input class="option-text" type="text" value="${escapeHtml(option.text || "")}" placeholder="Текст варианта">
    <button class="remove-row" type="button" aria-label="Удалить">×</button>`;
  $(".remove-row", row).addEventListener("click", () => row.remove());
  $("#choice-options").append(row);
}

function addPair(pair = {}) {
  const row = document.createElement("div");
  row.className = "pair-row";
  row.dataset.leftId = pair.leftId || rowId("left");
  row.dataset.rightId = pair.rightId || rowId("right");
  row.innerHTML = `
    <input class="pair-left" type="text" value="${escapeHtml(pair.left || "")}" placeholder="Левая часть">
    <span class="row-number">↔</span>
    <input class="pair-right" type="text" value="${escapeHtml(pair.right || "")}" placeholder="Правая часть">
    <button class="remove-row" type="button" aria-label="Удалить">×</button>`;
  $(".remove-row", row).addEventListener("click", () => row.remove());
  $("#matching-pairs").append(row);
}

function addOrderItem(item = {}) {
  const row = document.createElement("div");
  row.className = "order-row";
  row.dataset.id = item.id || rowId("order");
  row.innerHTML = `
    <span class="row-number"></span>
    <input class="order-text" type="text" value="${escapeHtml(item.text || "")}" placeholder="Пункт последовательности">
    <button class="remove-row" type="button" aria-label="Удалить">×</button>`;
  $(".remove-row", row).addEventListener("click", () => { row.remove(); renumberOrder(); });
  $("#ordering-items").append(row);
  renumberOrder();
}

function renumberOrder() {
  $$(".order-row", $("#ordering-items")).forEach((row, index) => { $(".row-number", row).textContent = index + 1; });
}

$("#add-option").addEventListener("click", () => addChoiceOption());
$("#add-pair").addEventListener("click", () => addPair());
$("#add-order-item").addEventListener("click", () => addOrderItem());

function formatBytes(value) {
  if (!value) return "0 КБ";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} КБ`;
  return `${(value / 1024 / 1024).toFixed(1)} МБ`;
}

function renderQuestionImage() {
  const image = state.questionImage;
  $("#question-image-empty").classList.toggle("hidden", Boolean(image));
  $("#question-image-preview").classList.toggle("hidden", !image);
  $("#question-image-id").value = image?.id || "";
  $("#question-image-url").value = image?.url || "";
  if (!image) {
    $("#question-image-preview-src").removeAttribute("src");
    return;
  }
  $("#question-image-preview-src").src = image.url;
  $("#question-image-name").textContent = image.filename || "Изображение вопроса";
  const size = image.byteSize ? formatBytes(image.byteSize) : "";
  const dimensions = image.width && image.height ? `${image.width} × ${image.height}` : "";
  $("#question-image-info").textContent = [dimensions, size].filter(Boolean).join(" · ");
}

async function uploadQuestionImage(file, input) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  showToast("Оптимизируем изображение…");
  input.disabled = true;
  try {
    const { data } = await api("/admin/media/images", { method: "POST", body: form });
    state.questionImage = data;
    renderQuestionImage();
    const saving = data.savedPercent ? `, размер уменьшен на ${data.savedPercent}%` : "";
    showToast(`Изображение загружено${saving}`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    input.disabled = false;
    input.value = "";
  }
}

$("#question-image-file").addEventListener("change", (event) => uploadQuestionImage(event.target.files[0], event.target));
$("#question-image-replace").addEventListener("change", (event) => uploadQuestionImage(event.target.files[0], event.target));
$("#remove-question-image").addEventListener("click", () => {
  state.questionImage = null;
  renderQuestionImage();
});

function showAnswerEditor() {
  const type = $("#question-type").value;
  $$(".answer-editor", $("#question-form")).forEach((editor) => editor.classList.add("hidden"));
  if (["single_choice", "multiple_choice"].includes(type)) {
    $("#choice-editor").classList.remove("hidden");
    const existing = $$(".option-row", $("#choice-options")).map((row) => ({
      id: row.dataset.id,
      text: $(".option-text", row).value,
      correct: $(".correct-choice", row).checked,
    }));
    $("#choice-options").innerHTML = "";
    (existing.length ? existing : [{}, {}]).forEach(addChoiceOption);
  } else if (type === "text") {
    $("#text-editor").classList.remove("hidden");
  } else if (type === "matching") {
    $("#matching-editor").classList.remove("hidden");
    if (!$("#matching-pairs").children.length) { addPair(); addPair(); }
  } else if (type === "ordering") {
    $("#ordering-editor").classList.remove("hidden");
    if (!$("#ordering-items").children.length) { addOrderItem(); addOrderItem(); }
  } else if (type === "self_check") {
    $("#self-check-editor").classList.remove("hidden");
  }
}

function clearQuestionValidation() {
  const warning = $("#question-validation-warning");
  warning.classList.add("hidden");
  warning.innerHTML = "";
}

function showQuestionValidation(messages) {
  const uniqueMessages = [...new Set(messages.filter(Boolean))];
  if (!uniqueMessages.length) return;
  const warning = $("#question-validation-warning");
  warning.innerHTML = uniqueMessages.length === 1
    ? escapeHtml(uniqueMessages[0])
    : `<strong>Вопрос заполнен не полностью:</strong><ul>${uniqueMessages.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul>`;
  warning.classList.remove("hidden");
  warning.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function questionValidationMessages(error) {
  const errors = error.data?.errors;
  if (Array.isArray(errors) && errors.length) {
    return errors.map((item) => item.message).filter(Boolean);
  }
  return [error.message];
}

$("#question-type").addEventListener("change", showAnswerEditor);
$("#question-subject").addEventListener("change", (event) => fillSections(event.target.value, $("#question-section")));
$("#question-form").addEventListener("input", clearQuestionValidation);
$("#question-form").addEventListener("change", clearQuestionValidation);

function resetQuestionForm() {
  $("#question-form").reset();
  clearQuestionValidation();
  $("#question-id").value = "";
  $("#question-subject").disabled = false;
  $("#question-type").disabled = false;
  $("#choice-options").innerHTML = "";
  $("#matching-pairs").innerHTML = "";
  $("#ordering-items").innerHTML = "";
  $("#accepted-answers").value = "";
  $("#model-answer").value = "";
  $("#case-sensitive").checked = false;
  state.questionEtag = null;
  state.questionStatus = null;
  state.questionImage = null;
  renderQuestionImage();
  $("#publish-question").classList.add("hidden");
  $("#archive-question").classList.add("hidden");
  showAnswerEditor();
}

$("#test-new-question-button").addEventListener("click", async () => {
  if (!state.activeTest) return;
  resetQuestionForm();
  $("#question-subject").value = state.activeTest.subject;
  $("#question-subject").disabled = true;
  $("#question-drawer-title").textContent = "Новый вопрос";
  await fillSections(state.activeTest.subject, $("#question-section"), state.activeTest.section?.id || "");
  openDrawer($("#question-drawer"));
});

function populateQuestionAnswer(detail) {
  $("#choice-options").innerHTML = "";
  $("#matching-pairs").innerHTML = "";
  $("#ordering-items").innerHTML = "";
  const prompt = detail.prompt || {};
  const answer = detail.answerSpec || {};
  if (["single_choice", "multiple_choice"].includes(detail.type)) {
    const correct = detail.type === "single_choice" ? [answer.correctOptionId] : (answer.correctOptionIds || []);
    (prompt.options || []).forEach((option) => addChoiceOption({ ...option, correct: correct.includes(option.id) }));
  } else if (detail.type === "text") {
    $("#accepted-answers").value = (answer.acceptedAnswers || []).join("\n");
    $("#case-sensitive").checked = Boolean(answer.caseSensitive);
  } else if (detail.type === "matching") {
    const rightById = new Map((prompt.rightItems || []).map((item) => [item.id, item.text]));
    (prompt.leftItems || []).forEach((left) => {
      const rightId = answer.pairs?.[left.id];
      addPair({ leftId: left.id, left: left.text, rightId, right: rightById.get(rightId) || "" });
    });
  } else if (detail.type === "ordering") {
    const itemById = new Map((prompt.items || []).map((item) => [item.id, item.text]));
    (answer.correctOrder || []).forEach((id) => addOrderItem({ id, text: itemById.get(id) || "" }));
  } else if (detail.type === "self_check") {
    const modelAnswer = answer.modelAnswer;
    $("#model-answer").value = Array.isArray(modelAnswer)
      ? modelAnswer.join("\n")
      : (modelAnswer || "");
  }
  showAnswerEditor();
}

async function editQuestion(id) {
  try {
    const { data, response } = await api(`/admin/questions/${id}`);
    resetQuestionForm();
    $("#question-id").value = data.id;
    $("#question-subject").value = data.subject;
    $("#question-subject").disabled = true;
    await fillSections(data.subject, $("#question-section"), data.sectionId || "");
    $("#question-type").value = data.type;
    $("#question-type").disabled = false;
    $("#question-text").value = data.prompt?.text || "";
    state.questionImage = data.prompt?.image || null;
    renderQuestionImage();
    $("#question-explanation").value = data.explanation?.text || "";
    $("#question-points").value = data.defaultPoints;
    $("#question-content-ids").value = (data.contentIds || []).join(", ");
    state.questionEtag = response.headers.get("ETag");
    state.questionStatus = data.status;
    $("#question-drawer-title").textContent = `Вопрос · версия ${data.currentVersion}`;
    $("#publish-question").classList.toggle("hidden", data.status === "published");
    $("#archive-question").classList.toggle("hidden", data.status === "archived");
    populateQuestionAnswer(data);
    openDrawer($("#question-drawer"));
  } catch (error) {
    showToast(error.message, true);
  }
}

function parseUuidList(value) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function buildQuestionPayload() {
  const type = $("#question-type").value;
  const questionText = $("#question-text").value.trim();
  if (!questionText) throw new Error("Нет текста вопроса");
  const prompt = { text: questionText };
  if (state.questionImage) {
    prompt.image = {
      id: state.questionImage.id,
      url: state.questionImage.url,
      filename: state.questionImage.filename,
      mimeType: state.questionImage.mimeType || "image/webp",
      byteSize: state.questionImage.byteSize,
      width: state.questionImage.width,
      height: state.questionImage.height,
      altText: state.questionImage.altText || null,
      checksumSha256: state.questionImage.checksumSha256 || null,
      createdAt: state.questionImage.createdAt || null,
    };
  }
  let answerSpec;
  if (["single_choice", "multiple_choice"].includes(type)) {
    const rows = $$(".option-row", $("#choice-options"));
    const options = rows.map((row) => ({ id: row.dataset.id, text: $(".option-text", row).value.trim() }));
    const correctIds = rows.filter((row) => $(".correct-choice", row).checked).map((row) => row.dataset.id);
    if (options.length < 2) throw new Error("Добавьте не менее двух вариантов ответа");
    if (options.some((option) => !option.text)) throw new Error("Заполните все варианты ответа");
    if (!correctIds.length) throw new Error("Нет ответа");
    prompt.options = options;
    answerSpec = type === "single_choice" ? { correctOptionId: correctIds[0] } : { correctOptionIds: correctIds };
  } else if (type === "text") {
    const acceptedAnswers = $("#accepted-answers").value.split("\n").map((item) => item.trim()).filter(Boolean);
    if (!acceptedAnswers.length) throw new Error("Нет ответа");
    answerSpec = { acceptedAnswers, caseSensitive: $("#case-sensitive").checked };
  } else if (type === "matching") {
    const rows = $$(".pair-row", $("#matching-pairs"));
    if (rows.length < 2) throw new Error("Нет ответа");
    if (rows.some((row) => !$(".pair-left", row).value.trim() || !$(".pair-right", row).value.trim())) throw new Error("Нет ответа");
    prompt.leftItems = rows.map((row) => ({ id: row.dataset.leftId, text: $(".pair-left", row).value.trim() }));
    prompt.rightItems = rows.map((row) => ({ id: row.dataset.rightId, text: $(".pair-right", row).value.trim() }));
    answerSpec = { pairs: Object.fromEntries(rows.map((row) => [row.dataset.leftId, row.dataset.rightId])) };
  } else if (type === "ordering") {
    const rows = $$(".order-row", $("#ordering-items"));
    if (rows.length < 2) throw new Error("Нет ответа");
    if (rows.some((row) => !$(".order-text", row).value.trim())) throw new Error("Нет ответа");
    prompt.items = rows.map((row) => ({ id: row.dataset.id, text: $(".order-text", row).value.trim() }));
    answerSpec = { correctOrder: rows.map((row) => row.dataset.id) };
  } else if (type === "self_check") {
    const modelAnswer = $("#model-answer").value.trim();
    if (!modelAnswer) throw new Error("Нет ответа");
    answerSpec = { modelAnswer };
  } else {
    throw new Error("Этот тип вопроса пока нельзя редактировать в панели");
  }
  return {
    subject: $("#question-subject").value,
    sectionId: $("#question-section").value || null,
    type,
    prompt,
    answerSpec,
    explanation: { text: $("#question-explanation").value.trim() },
    defaultPoints: Number($("#question-points").value),
    contentIds: parseUuidList($("#question-content-ids").value),
  };
}

async function persistQuestion(payload, id) {
  if (id) {
    const patch = { ...payload };
    delete patch.subject;
    delete patch.testId;
    return api(`/admin/questions/${id}`, {
      method: "PATCH",
      contentType: "application/merge-patch+json",
      headers: { "If-Match": state.questionEtag },
      body: patch,
    });
  }
  return api("/admin/questions", {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: payload,
  });
}

$("#question-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true);
  try {
    clearQuestionValidation();
    const payload = buildQuestionPayload();
    const id = $("#question-id").value;
    payload.tagIds = [];
    payload.testId = state.activeTest?.id || null;
    let result;
    try {
      result = await persistQuestion(payload, id);
    } catch (error) {
      if (error.code !== "duplicate_question") throw error;
      const similarity = error.data?.errors?.[0]?.similarity;
      const percent = similarity ? ` (сходство ${Math.round(similarity * 100)}%)` : "";
      const shouldSave = confirm(`Такой вопрос уже существует${percent}.\n\nСохранить всё равно?`);
      if (!shouldSave) {
        showQuestionValidation(["Сохранение отменено: такой вопрос уже существует"]);
        return;
      }
      payload.allowDuplicate = true;
      result = await persistQuestion(payload, id);
    }
    if (!id) $("#question-id").value = result.data.id;
    state.questionEtag = result.response.headers.get("ETag");
    state.questionStatus = result.data.status;
    $("#question-drawer-title").textContent = `Вопрос · версия ${result.data.currentVersion}`;
    $("#publish-question").classList.toggle("hidden", result.data.status === "published");
    $("#archive-question").classList.toggle("hidden", result.data.status === "archived");
    showToast("Вопрос сохранён");
    if (state.activeTest) await refreshActiveTest();
    else if (state.questionBankOpen) await loadQuestionBank(true);
    else if (state.historyTrainerOpen) {
      await Promise.all([loadHistoryTrainerStatus(), loadHistoryTrainerQuestions(true)]);
    }
  } catch (error) {
    showQuestionValidation(questionValidationMessages(error));
    showToast(error.message, true);
  } finally {
    setBusy(button, false);
  }
});

async function changeQuestionStatus(action) {
  const id = $("#question-id").value;
  if (!id) return;
  if (action === "archive" && !confirm("Переместить вопрос в архив?")) return;
  try {
    await api(`/admin/questions/${id}/${action}`, {
      method: "POST",
      headers: { "If-Match": state.questionEtag, "Idempotency-Key": crypto.randomUUID() },
    });
    showToast(action === "publish" ? "Вопрос опубликован" : "Вопрос перемещён в архив");
    closeDrawers();
    if (state.activeTest) await refreshActiveTest();
    else if (state.questionBankOpen) await loadQuestionBank(true);
    else if (state.historyTrainerOpen) {
      await Promise.all([loadHistoryTrainerStatus(), loadHistoryTrainerQuestions(true)]);
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

$("#publish-question").addEventListener("click", () => changeQuestionStatus("publish"));
$("#archive-question").addEventListener("click", () => changeQuestionStatus("archive"));

function renderQuestionBankItems(items, append) {
  const list = $("#question-bank-list");
  const html = items.map((item) => `
    <article class="content-card question-bank-card" data-id="${item.id}">
      <div><span class="badge ${item.status}">${statusLabel(item.status)}</span>
        <h3 class="card-title">${escapeHtml(item.promptPreview || "Без текста вопроса")}</h3>
        <div class="card-meta"><span>${subjectLabel(item.subject)}</span><span>${typeLabel(item.type)}</span><span>${item.testCount ? `В тестах: ${item.testCount}` : "Не добавлен в тесты"}</span><span>Версия ${item.currentVersion}</span></div>
      </div>
      <div class="card-actions"><button class="button secondary open-bank-question" data-id="${item.id}" type="button">Открыть</button></div>
    </article>`).join("");
  if (append) list.insertAdjacentHTML("beforeend", html);
  else list.innerHTML = html;
  $$(".open-bank-question", list).forEach((button) => button.addEventListener("click", () => editQuestion(button.dataset.id)));
  $$(".question-bank-card", list).forEach((card) => card.addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) editQuestion(card.dataset.id);
  }));
}

async function loadQuestionBank(reset = true) {
  const list = $("#question-bank-list");
  if (reset) {
    state.questionBankCursor = null;
    list.innerHTML = '<div class="loading">Загружаем банк вопросов…</div>';
  }
  const params = new URLSearchParams({
    limit: "100",
    assignment: $("#question-bank-assignment").value,
  });
  const subject = $("#question-bank-subject").value;
  const status = $("#question-bank-status").value;
  const search = $("#question-bank-search").value.trim();
  if (subject) params.set("subject", subject);
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  if (!reset && state.questionBankCursor) params.set("cursor", state.questionBankCursor);
  try {
    const { data } = await api(`/admin/questions?${params}`);
    if (reset && !data.items.length) {
      renderEmpty(list, "Вопросы не найдены", "Измените фильтры или создайте вопрос внутри теста.");
    } else {
      renderQuestionBankItems(data.items, !reset);
    }
    state.questionBankCursor = data.page.nextCursor;
    $("#load-more-question-bank").classList.toggle("hidden", !data.page.hasMore);
  } catch (error) {
    renderEmpty(list, "Не удалось загрузить банк вопросов", error.message);
  }
}

async function openQuestionBank() {
  state.activeTest = null;
  state.activeTestQuestions = [];
  state.questionBankOpen = true;
  state.historyTrainerOpen = false;
  $("#tests-picker").classList.add("hidden");
  $("#test-workspace").classList.add("hidden");
  $("#question-bank-workspace").classList.remove("hidden");
  await loadQuestionBank(true);
}

$("#open-question-bank").addEventListener("click", openQuestionBank);
$("#back-from-question-bank").addEventListener("click", showTestsPicker);
$("#refresh-question-bank").addEventListener("click", () => loadQuestionBank(true));
$("#question-bank-subject").addEventListener("change", () => loadQuestionBank(true));
$("#question-bank-assignment").addEventListener("change", () => loadQuestionBank(true));
$("#question-bank-status").addEventListener("change", () => loadQuestionBank(true));
$("#question-bank-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadQuestionBank(true);
});
$("#load-more-question-bank").addEventListener("click", () => loadQuestionBank(false));

function renderHistoryTrainerStatus(data) {
  const summary = $("#history-trainer-summary");
  if (!data.imported) {
    summary.textContent = "База вопросов ещё не импортирована";
  } else {
    summary.textContent = `${data.totalQuestionCount} вопросов · ${data.taskCount} типов заданий · ${data.imageCount} изображений`;
  }
  const details = $("#history-trainer-details");
  if (!data.imported) {
    details.innerHTML = `
      <div class="empty-state">
        <h3>Сначала импортируйте SQLite-базу</h3>
        <p class="muted">После импорта здесь появятся все 21 тип задания и их количество. Команда приведена в инструкции обновления.</p>
      </div>`;
    return;
  }
  details.innerHTML = `
    <div class="stats-grid">
      <article class="stat-card"><strong>${data.totalQuestionCount}</strong><span>всего вопросов</span></article>
      <article class="stat-card"><strong>${data.publishedQuestionCount}</strong><span>доступны ученикам</span></article>
      <article class="stat-card review-stat"><strong>${data.reviewRequiredCount}</strong><span>требуют проверки</span></article>
      <article class="stat-card"><strong>${data.imageCount}</strong><span>изображений</span></article>
    </div>`;
  const taskFilter = $("#trainer-task-filter");
  const selectedTask = taskFilter.value;
  taskFilter.innerHTML = '<option value="">Все номера заданий</option>' + data.tasks.map((item) =>
    `<option value="${item.number}">Задание № ${item.number} — ${item.questionCount}</option>`).join("");
  if ($(`option[value="${selectedTask}"]`, taskFilter)) taskFilter.value = selectedTask;
}

async function loadHistoryTrainerStatus() {
  try {
    const { data } = await api("/admin/history-trainer/status");
    renderHistoryTrainerStatus(data);
  } catch (error) {
    $("#history-trainer-summary").textContent = "Не удалось проверить состояние тренажёра";
    renderEmpty($("#history-trainer-details"), "Ошибка загрузки", error.message);
  }
}

function renderHistoryTrainerQuestions(items, append) {
  const list = $("#trainer-question-list");
  const html = items.map((item) => `
    <article class="content-card trainer-question-card ${item.image?.url ? "has-image" : "no-image"}${item.reviewRequired ? " requires-review" : ""}" data-id="${item.id}">
      ${item.image?.url ? `<div class="trainer-question-thumbnail"><img class="question-card-image" src="${escapeHtml(item.image.url)}" alt=""></div>` : ""}
      <div>
        <div class="trainer-question-badges">
          <span class="badge ${item.status}">${statusLabel(item.status)}</span>
          <span class="badge task-badge">Задание № ${item.taskNumber}</span>
          ${item.reviewRequired ? '<span class="badge review-required">Требует проверки</span>' : '<span class="badge review-ready">Проверен</span>'}
        </div>
        <h3 class="card-title">${escapeHtml(item.promptPreview || "Без текста вопроса")}</h3>
        ${item.reviewRequired && item.reviewReasons.length ? `<div class="review-reasons">${item.reviewReasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>` : ""}
        <div class="card-meta">
          <span>${typeLabel(item.type)}</span>
          ${item.period ? `<span>${escapeHtml(item.period)}</span>` : ""}
          <span>Источник № ${item.sourceRowId}</span>
          <span>Версия ${item.currentVersion}</span>
        </div>
      </div>
      <div class="card-actions"><button class="button secondary open-trainer-question" data-id="${item.id}" type="button">Редактировать</button></div>
    </article>`).join("");
  if (append) list.insertAdjacentHTML("beforeend", html);
  else list.innerHTML = html;
  $$(".open-trainer-question", list).forEach((button) =>
    button.addEventListener("click", () => editQuestion(button.dataset.id)));
  $$(".trainer-question-card", list).forEach((card) => card.addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) editQuestion(card.dataset.id);
  }));
}

async function loadHistoryTrainerQuestions(reset = true) {
  const list = $("#trainer-question-list");
  if (reset) {
    state.historyTrainerCursor = null;
    list.innerHTML = '<div class="loading">Загружаем вопросы тренажёра…</div>';
  }
  const params = new URLSearchParams({
    limit: "50",
    review: $("#trainer-review-filter").value,
  });
  const task = $("#trainer-task-filter").value;
  const status = $("#trainer-status-filter").value;
  const search = $("#trainer-question-search").value.trim();
  if (task) params.set("taskNumber", task);
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  if (!reset && state.historyTrainerCursor) params.set("cursor", state.historyTrainerCursor);
  try {
    const { data } = await api(`/admin/history-trainer/questions?${params}`);
    if (reset && !data.items.length) {
      renderEmpty(list, "Вопросы не найдены", "Измените номер задания или фильтр проверки.");
    } else {
      renderHistoryTrainerQuestions(data.items, !reset);
    }
    state.historyTrainerCursor = data.page.nextCursor;
    $("#load-more-trainer-questions").classList.toggle("hidden", !data.page.hasMore);
  } catch (error) {
    renderEmpty(list, "Не удалось загрузить вопросы", error.message);
  }
}

async function openHistoryTrainer() {
  state.historyTrainerOpen = true;
  state.questionBankOpen = false;
  state.activeTest = null;
  await Promise.all([loadHistoryTrainerStatus(), loadHistoryTrainerQuestions(true)]);
}

$("#refresh-trainer-questions").addEventListener("click", () => loadHistoryTrainerQuestions(true));
$("#trainer-task-filter").addEventListener("change", () => loadHistoryTrainerQuestions(true));
$("#trainer-review-filter").addEventListener("change", () => loadHistoryTrainerQuestions(true));
$("#trainer-status-filter").addEventListener("change", () => loadHistoryTrainerQuestions(true));
$("#trainer-question-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadHistoryTrainerQuestions(true);
});
$("#load-more-trainer-questions").addEventListener("click", () => loadHistoryTrainerQuestions(false));

async function loadTests() {
  const list = $("#tests-list");
  list.innerHTML = '<div class="loading">Загружаем тесты…</div>';
  const params = new URLSearchParams({ limit: "100" });
  const subject = $("#test-subject-filter").value;
  const status = $("#test-status-filter").value;
  if (subject) params.set("subject", subject);
  if (status) params.set("status", status);
  try {
    const { data } = await api(`/admin/tests?${params}`);
    const regularTests = data.items.filter((item) => item.slug !== "history-trainer");
    if (!regularTests.length) {
      renderEmpty(list, "Тестов пока нет", "Создайте тест, а затем добавьте в него вопросы.");
      return;
    }
    list.innerHTML = regularTests.map((item) => `
      <article class="content-card editable-test" data-id="${item.id}">
        <div><span class="badge ${item.status}">${statusLabel(item.status)}</span>
          <h3 class="card-title">${escapeHtml(item.title)}</h3>
          <div class="card-meta"><span>${subjectLabel(item.subject)}</span><span>${item.questionCount} вопросов</span><span>${item.totalPoints} баллов</span><span>${item.timeLimitSeconds ? `${item.timeLimitSeconds} сек.` : "Без таймера"}</span></div>
        </div>
        <div class="card-actions"><button class="button secondary open-test" data-id="${item.id}" type="button">Открыть</button></div>
      </article>`).join("");
    $$(".open-test", list).forEach((button) => button.addEventListener("click", () => openTestWorkspace(button.dataset.id)));
    $$(".editable-test", list).forEach((card) => card.addEventListener("dblclick", (event) => {
      if (!event.target.closest("button")) openTestWorkspace(card.dataset.id);
    }));
  } catch (error) {
    if (error.status === 401) return logout("Сессия закончилась. Войдите снова.");
    renderEmpty(list, "Не удалось загрузить тесты", error.message);
  }
}

$("#refresh-tests").addEventListener("click", loadTests);
$("#test-subject-filter").addEventListener("change", loadTests);
$("#test-status-filter").addEventListener("change", loadTests);
$("#test-subject").addEventListener("change", (event) => fillSections(event.target.value, $("#test-section")));

function renderActiveTest() {
  const test = state.activeTest;
  if (!test) return;
  $("#active-test-subject").textContent = subjectLabel(test.subject);
  $("#active-test-title").textContent = test.title;
  $("#active-test-meta").textContent = `${test.questionCount} вопросов · ${test.totalPoints} баллов · ${test.timeLimitSeconds ? `${test.timeLimitSeconds} сек.` : "без ограничения времени"}`;
  $("#publish-active-test").classList.toggle("hidden", test.status !== "draft");
  $("#archive-active-test").classList.toggle("hidden", test.status === "archived");
  $("#test-new-question-button").classList.toggle("hidden", test.status === "archived");
}

function renderActiveTestQuestions() {
  const list = $("#test-questions-list");
  if (!state.activeTestQuestions.length) {
    renderEmpty(list, "В тесте пока нет вопросов", "Нажмите «Новый вопрос», чтобы добавить первое задание.");
    return;
  }
  list.innerHTML = state.activeTestQuestions.map((item, index) => `
    <article class="content-card test-question-card">
      ${item.image?.url ? `<img class="question-card-image" src="${escapeHtml(item.image.url)}" alt="">` : ""}
      <div class="test-question-position">${index + 1}</div>
      <div><span class="badge ${item.status}">${statusLabel(item.status)}</span>
        <h3 class="card-title">${escapeHtml(item.promptPreview || `Вопрос ${item.id.slice(0, 8)}`)}</h3>
        <div class="card-meta"><span>${typeLabel(item.type)}</span><span>${item.points} балл.</span><span>Версия ${item.currentVersion}</span></div>
      </div>
      <div class="card-actions test-question-actions">
        <button class="reorder-button move-test-question-up" data-index="${index}" type="button" aria-label="Переместить выше" ${index === 0 ? "disabled" : ""}>↑</button>
        <button class="reorder-button move-test-question-down" data-index="${index}" type="button" aria-label="Переместить ниже" ${index === state.activeTestQuestions.length - 1 ? "disabled" : ""}>↓</button>
        <button class="button secondary edit-test-question" data-id="${item.id}" type="button">Открыть</button>
        <button class="button danger compact detach-test-question" data-index="${index}" type="button">Убрать</button>
      </div>
    </article>`).join("");
  $$(".edit-test-question", list).forEach((button) => button.addEventListener("click", () => editQuestion(button.dataset.id)));
  $$(".move-test-question-up", list).forEach((button) => button.addEventListener("click", () => moveTestQuestion(Number(button.dataset.index), -1)));
  $$(".move-test-question-down", list).forEach((button) => button.addEventListener("click", () => moveTestQuestion(Number(button.dataset.index), 1)));
  $$(".detach-test-question", list).forEach((button) => button.addEventListener("click", () => detachTestQuestion(Number(button.dataset.index))));
}

async function refreshActiveTest() {
  if (!state.activeTest) return;
  const id = state.activeTest.id;
  const [{ data: test, response }, { data: questions }] = await Promise.all([
    api(`/admin/tests/${id}`), api(`/admin/tests/${id}/questions`),
  ]);
  state.activeTest = test;
  state.activeTestQuestions = questions.items;
  state.testEtag = response.headers.get("ETag");
  state.testStatus = test.status;
  renderActiveTest();
  renderActiveTestQuestions();
}

async function openTestWorkspace(id) {
  state.questionBankOpen = false;
  state.historyTrainerOpen = false;
  state.activeTest = { id };
  $("#tests-picker").classList.add("hidden");
  $("#question-bank-workspace").classList.add("hidden");
  $("#test-workspace").classList.remove("hidden");
  $("#test-questions-list").innerHTML = '<div class="loading">Загружаем вопросы теста…</div>';
  try { await refreshActiveTest(); }
  catch (error) { showToast(error.message, true); await showTestsPicker(); }
}

async function showTestsPicker() {
  state.activeTest = null;
  state.activeTestQuestions = [];
  state.questionBankOpen = false;
  state.historyTrainerOpen = false;
  $("#test-workspace").classList.add("hidden");
  $("#question-bank-workspace").classList.add("hidden");
  $("#tests-picker").classList.remove("hidden");
  await loadTests();
}

$("#back-to-tests").addEventListener("click", showTestsPicker);

async function saveTestQuestions(items) {
  const questions = items.map((item, index) => ({
    questionId: item.id, position: index + 1,
    pointsOverride: item.pointsOverride ?? null, required: item.required ?? true,
  }));
  await api(`/admin/tests/${state.activeTest.id}`, {
    method: "PATCH", contentType: "application/merge-patch+json",
    headers: { "If-Match": state.testEtag }, body: { questions },
  });
  await refreshActiveTest();
}

async function moveTestQuestion(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= state.activeTestQuestions.length) return;
  const items = [...state.activeTestQuestions];
  [items[index], items[target]] = [items[target], items[index]];
  try { await saveTestQuestions(items); } catch (error) { showToast(error.message, true); }
}

async function detachTestQuestion(index) {
  if (!confirm("Убрать вопрос из этого теста? Сам вопрос останется в базе.")) return;
  try {
    await saveTestQuestions(state.activeTestQuestions.filter((_, itemIndex) => itemIndex !== index));
    showToast("Вопрос убран из теста");
  } catch (error) { showToast(error.message, true); }
}

function resetTestForm() {
  $("#test-form").reset();
  $("#test-id").value = "";
  $("#test-subject").disabled = false;
  $("#publish-test").classList.add("hidden");
  $("#archive-test").classList.add("hidden");
}

$("#new-test-button").addEventListener("click", async () => {
  resetTestForm();
  $("#test-drawer-title").textContent = "Новый тест";
  await fillSections($("#test-subject").value, $("#test-section"));
  openDrawer($("#test-drawer"));
});

async function openTestSettings() {
  if (!state.activeTest) return;
  const data = state.activeTest;
  const currentEtag = state.testEtag;
  resetTestForm();
  state.testEtag = currentEtag;
  $("#test-id").value = data.id;
  $("#test-subject").value = data.subject;
  $("#test-subject").disabled = true;
  $("#test-title").value = data.title;
  $("#test-description").value = data.description || "";
  $("#test-time-limit").value = data.timeLimitSeconds || "";
  await fillSections(data.subject, $("#test-section"), data.section?.id || "");
  $("#test-drawer-title").textContent = data.title;
  openDrawer($("#test-drawer"));
}

$("#test-settings-button").addEventListener("click", openTestSettings);

$("#test-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true);
  try {
    const common = {
      sectionId: $("#test-section").value || null,
      title: $("#test-title").value.trim(),
      description: $("#test-description").value.trim() || null,
      timeLimitSeconds: $("#test-time-limit").value ? Number($("#test-time-limit").value) : null,
    };
    const id = $("#test-id").value;
    const result = id
      ? await api(`/admin/tests/${id}`, {
        method: "PATCH", contentType: "application/merge-patch+json",
        headers: { "If-Match": state.testEtag }, body: common,
      })
      : await api("/admin/tests", {
        method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
        body: { subject: $("#test-subject").value, mode: "fixed", questions: [], blueprint: null, ...common },
      });
    showToast("Тест сохранён");
    closeDrawers();
    await openTestWorkspace(result.data.id);
  } catch (error) { showToast(error.message, true); }
  finally { setBusy(button, false); }
});

async function changeActiveTestStatus(action) {
  const id = state.activeTest?.id;
  if (!id) return;
  if (action === "archive" && !confirm("Переместить тест в архив?")) return;
  try {
    await api(`/admin/tests/${id}/${action}`, {
      method: "POST",
      headers: { "If-Match": state.testEtag, "Idempotency-Key": crypto.randomUUID() },
    });
    showToast(action === "publish" ? "Тест опубликован" : "Тест перемещён в архив");
    await refreshActiveTest();
  } catch (error) { showToast(error.message, true); }
}

$("#publish-active-test").addEventListener("click", () => changeActiveTestStatus("publish"));
$("#archive-active-test").addEventListener("click", () => changeActiveTestStatus("archive"));

function formatReportDate(value) {
  return value ? new Date(value).toLocaleString("ru-RU") : "";
}

function renderErrorReports(items, append) {
  const list = $("#error-reports-list");
  const html = items.map((item) => `
    <details class="error-report-card report-accordion${item.status === "open" ? " open" : " answered"}" data-id="${item.id}">
      <summary><strong>От ${escapeHtml(item.reporter?.name||'Ученик')} (${escapeHtml(item.reporter?.email||'Без почты')})</strong><span>${escapeHtml(Array.from(item.message||'').slice(0,100).join(''))}${Array.from(item.message||'').length>100?'…':''}</span></summary>
      <div class="report-accordion-body"><div class="error-report-heading">
        <div class="trainer-question-badges">
          <span class="badge ${item.status === "open" ? "review-required" : "review-ready"}">${({new:"Новое",in_progress:"В работе",resolved:"Решено"})[item.workflowStatus] || "Новое"}</span>
          ${item.taskNumber ? `<span class="badge task-badge">Задание № ${item.taskNumber}</span>` : ""}
          ${item.questionVersion ? `<span class="badge">Версия ${item.questionVersion}</span>` : `<span class="badge">Обращение из кабинета</span>`}
        </div>
        <time>${formatReportDate(item.createdAt)}</time>
      </div>
      <h3>${escapeHtml(item.questionPreview || "Обращение ученика")}</h3>
      <div class="reporter-line">${item.reporter ? `${escapeHtml(item.reporter.name || "Ученик")} · ${escapeHtml(item.reporter.email)}` : "Анонимный ученик"}</div>
      <div class="student-report-message"><strong>Сообщение ученика</strong><p>${escapeHtml(item.message)}</p></div>
      <label class="admin-reply-field">Ответ ученику
        <textarea class="error-report-reply" rows="4" maxlength="3000" placeholder="Напишите, исправлена ли ошибка и что изменилось">${escapeHtml(item.adminReply || "")}</textarea>
      </label>
      <div class="card-actions">
        <label>Статус <select class="report-workflow" data-id="${item.id}">${[['new','Новое'],['in_progress','В работе'],['resolved','Решено']].map(([v,t])=>`<option value="${v}" ${item.workflowStatus===v?'selected':''}>${t}</option>`).join('')}</select></label>
        ${item.questionId ? `<button class="button secondary open-reported-question" data-question-id="${item.questionId}" type="button">Редактировать вопрос теста</button>` : ""}
        <button class="button primary send-error-report-reply" data-id="${item.id}" type="button">${item.adminReply ? "Обновить ответ" : "Ответить ученику"}</button>
      </div>
      </div></details>`).join("");
  if (append) list.insertAdjacentHTML("beforeend", html);
  else list.innerHTML = html;
  $$(".report-workflow",list).forEach(select=>select.onchange=async()=>{select.disabled=true;try{await api(`/admin/question-error-reports/${select.dataset.id}/status`,{method:'PATCH',body:{status:select.value}});showToast('Статус обновлён');await loadErrorReports(true);}catch(e){showToast(e.message,true);await loadErrorReports(true);}});
  $$(".open-reported-question", list).forEach((button) =>
    button.addEventListener("click", () => editQuestion(button.dataset.questionId)));
  $$(".send-error-report-reply", list).forEach((button) =>
    button.addEventListener("click", () => replyToErrorReport(button)));
}

function updateErrorReportBadge(count) {
  const badge = $("#error-reports-nav-badge");
  badge.textContent = String(count);
  badge.classList.toggle("hidden", !count);
}

async function loadErrorReports(reset = true) {
  const list = $("#error-reports-list");
  if (reset) {
    state.errorReportsCursor = null;
    list.innerHTML = '<div class="loading">Загружаем сообщения учеников…</div>';
  }
  const params = new URLSearchParams({
    status: $("#error-report-status-filter").value,
    limit: "50",
  });
  if (!reset && state.errorReportsCursor) params.set("cursor", state.errorReportsCursor);
  try {
    const { data } = await api(`/admin/question-error-reports?${params}`);
    updateErrorReportBadge(data.openCount);
    if (reset && !data.items.length) {
      renderEmpty(list, "Сообщений нет", "Новые обращения учеников появятся в этом разделе.");
    } else {
      renderErrorReports(data.items, !reset);
    }
    state.errorReportsCursor = data.page.nextCursor;
    $("#load-more-error-reports").classList.toggle("hidden", !data.page.hasMore);
  } catch (error) {
    renderEmpty(list, "Не удалось загрузить сообщения", error.message);
  }
}

async function replyToErrorReport(button) {
  const card = button.closest(".error-report-card");
  const message = $(".error-report-reply", card).value.trim();
  if (message.length < 2) {
    showToast("Введите ответ ученику", true);
    return;
  }
  setBusy(button, true);
  try {
    await api(`/admin/question-error-reports/${button.dataset.id}/reply`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: { message },
    });
    showToast("Ответ отправлен ученику");
    await loadErrorReports(true);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

$("#refresh-error-reports").addEventListener("click", () => loadErrorReports(true));
$("#error-report-status-filter").addEventListener("change", () => loadErrorReports(true));
$("#load-more-error-reports").addEventListener("click", () => loadErrorReports(false));

restoreSession();

if(location.hash==="#messages")$("[data-view='error-reports']").click();
setInterval(async()=>{
  if(!state.user)return;
  try{const {data}=await api("/admin/question-error-reports?status=open&limit=1");updateErrorReportBadge(data.openCount);}catch{}
},60000);


let studentsOffset=0,studentsGeneration=0,studentDetailGeneration=0;
const studentDate=value=>value?new Date(value).toLocaleString('ru-RU'):'—';
const studentEl=(tag,text,className)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(className)e.className=className;return e;};
$('#students-search').onsubmit=e=>{e.preventDefault();loadStudents(true);};
['students-status','students-subject','students-inactive'].forEach(id=>$('#'+id).onchange=()=>loadStudents(true));
$('#students-more').onclick=()=>loadStudents(false);
async function loadStudents(reset){
  const generation=reset?++studentsGeneration:studentsGeneration;
  if(reset){studentsOffset=0;$('#students-list').replaceChildren();studentDetailGeneration++;}
  $('#students-more').disabled=true;$('#students-notice').textContent='Загружаем…';
  try{
    const params=studentFilters();params.set('offset',studentsOffset);params.set('limit',50);
    const {data}=await api(`/admin/students?${params}`);if(generation!==studentsGeneration)return;
    data.items.forEach(u=>{
      const card=studentEl('details',null,'student-accordion'),heading=studentEl('summary',`${u.name||'Имя не указано'} (${u.email||'Почта не указана'})`),content=studentEl('div',null,'student-detail');
      card.setAttribute('name','students');card.dataset.studentId=u.id;content.dataset.studentId=u.id;card.append(heading,content);$('#students-list').append(card);
      card.addEventListener('toggle',()=>{if(!card.open)return;$$('.student-accordion').forEach(other=>{if(other!==card)other.open=false;});openStudent(u.id);});
    });
    studentsOffset+=data.items.length;$('#students-more').classList.toggle('hidden',!data.hasMore);$('#students-notice').textContent=studentsOffset?`Найдено учеников: ${data.total}.`:'Ученики не найдены.';
  }catch(e){if(generation===studentsGeneration)$('#students-notice').textContent=e.message;}finally{if(generation===studentsGeneration)$('#students-more').disabled=false;}
}
async function openStudent(id){
  const card=$$('.student-accordion').find(c=>c.dataset.studentId===id);if(!card)return;if(!card.open){card.open=true;return;}const root=card.querySelector('.student-detail');const generation=++studentDetailGeneration;root.textContent='Загружаем карточку…';
  try{
    const {data}=await api(`/admin/students/${encodeURIComponent(id)}`);if(generation!==studentDetailGeneration||!root.isConnected||!card.open)return;root.replaceChildren();const u=data.student;
    root.append(studentEl('h2',u.name||'Имя не указано'),studentEl('p',u.email),studentEl('p',`Регистрация: ${studentDate(u.registeredAt)} · Последняя активность: ${studentDate(u.lastActivity)}`));
    renderStudentNotes(root,u,data.weakTopics);
    if(data.mistakeReview)root.append(studentEl('p',`Работа над ошибками · Осталось: ${data.mistakeReview.pending} · Отработано: ${data.mistakeReview.corrected}`));
    if(window.renderProgress)window.renderProgress(root,`/admin/students/${encodeURIComponent(id)}/progress`,u.registeredAt);
    const access=studentEl('button',u.status==='blocked'?'Разблокировать ученика':'Заблокировать ученика','button '+(u.status==='blocked'?'primary':'danger student-block'));access.type='button';root.append(access);
    access.onclick=async()=>{if(!confirm(u.status==='blocked'?'Разрешить ученику вход в аккаунт?':`Заблокировать ученика ${u.name||''} (${u.email})? Действующие сессии будут завершены.`))return;access.disabled=true;try{await api(`/admin/students/${encodeURIComponent(id)}/access`,{method:'PATCH',body:{blocked:u.status!=='blocked'}});showToast('Доступ обновлён');await loadStudents(true);await openStudent(id);}catch(e){showToast(e.message,true);access.disabled=false;}};
    const searches=studentEl('details');searches.append(studentEl('summary',`Поиски · последние ${data.searches.length} (до 100)`));
    const categories={'history-term':'Термины по истории','history-date':'Даты','society-term':'Термины по обществознанию','society-plan':'Планы'};
    data.searches.forEach(h=>{const row=studentEl('div',null,'student-history-row');row.append(studentEl('strong',h.query),studentEl('small',`${studentDate(h.createdAt)} · ${categories[`${h.subject}-${h.type}`]||'Поиск'}`));searches.append(row);});if(!data.searches.length)searches.append(studentEl('p','Сохранённых поисков пока нет.'));root.append(searches);root.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){if(generation===studentDetailGeneration)root.textContent=e.message;}
}

function studentFilters(){const params=new URLSearchParams({q:$('#students-query').value.trim(),group:$('#students-group').value.trim(),inactive_days:$('#students-inactive').value});for(const [key,id] of [['status','students-status'],['subject','students-subject']])if($('#'+id).value)params.set(key,$('#'+id).value);return params;}
$('#students-export').onclick=async()=>{const button=$('#students-export');button.disabled=true;try{const response=await window.accountRequest(`/admin/students/export.xlsx?${studentFilters()}`);if(!response.ok){let p={};try{p=await response.json();}catch{}throw Error(p.detail||p.title||'Не удалось выгрузить Excel');}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='students-results.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){showToast(e.message,true);}finally{button.disabled=false;}};
function renderStudentNotes(root,u,weak){
 const form=studentEl('form',null,'student-notes'),notes=studentEl('textarea'),groups=studentEl('input');notes.rows=4;notes.maxLength=10000;notes.value=u.notes;groups.maxLength=1620;groups.value=u.groups.join(', ');
 const field=(text,input)=>{const label=studentEl('label',text);label.append(input);form.append(label);};field('Заметки администратора (видны только администраторам)',notes);field('Группы через запятую',groups);
 const subjects=studentEl('div'),checks=[];['history','society'].forEach(code=>{const label=studentEl('label',code==='history'?'История':'Обществознание'),check=studentEl('input');check.type='checkbox';check.checked=u.subjects.includes(code);label.prepend(check);subjects.append(label);checks.push([code,check]);});form.append(studentEl('p','Предметы ученика. Если ничего не выбрано, определяются по его поискам и тренировкам.'),subjects);
 const save=studentEl('button','Сохранить заметки, группы и предметы','button primary'),notice=studentEl('p');save.type='submit';notice.setAttribute('role','status');form.append(save,notice);form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await api(`/admin/students/${encodeURIComponent(u.id)}/notes`,{method:'PUT',body:{notes:notes.value,groups:groups.value.split(','),subjects:checks.filter(([,check])=>check.checked).map(([code])=>code)}});showToast('Заметки, группы и предметы сохранены');await loadStudents(true);await openStudent(u.id);}catch(err){notice.textContent=err.message;}finally{save.disabled=false;}};root.append(form);
 renderWeakTopicRange(root,weak);

}

let auditOffset=0,auditGeneration=0;
$('#audit-search').onsubmit=e=>{e.preventDefault();loadAdminJournal(true);};
$('#audit-more').onclick=()=>loadAdminJournal(false);
$('#audit-event').onchange=()=>loadAdminJournal(true);
function auditAction(action){
 const method=action.split(' ')[0],path=action.slice(method.length+1);
 if(path.endsWith('/notes'))return 'Изменение заметок, групп и предметов ученика';
 if(path.endsWith('/access'))return 'Изменение доступа ученика';
 if(path.endsWith('/reply'))return 'Ответ на обращение';
 if(path.endsWith('/status')&&path.includes('question-error-reports'))return 'Изменение статуса обращения';
 if(path.endsWith('/export.xlsx'))return 'Выгрузка учеников и результатов в Excel';
 return `${method==='DELETE'?'Удаление':method==='POST'?'Действие': 'Изменение'}: ${path}`;
}
async function loadAdminJournal(reset){const generation=reset?++auditGeneration:auditGeneration;if(reset){auditOffset=0;$('#audit-list').replaceChildren();}$('#audit-more').disabled=true;$('#audit-notice').textContent='Загружаем…';try{const {data}=await api(`/admin/students/audit?${new URLSearchParams({q:$('#audit-query').value.trim(),event:$('#audit-event').value,offset:auditOffset,limit:50})}`);if(generation!==auditGeneration)return;data.items.forEach(item=>{const card=studentEl('article',null,'student-history-row');card.append(studentEl('small',studentDate(item.createdAt)),studentEl('strong',auditAction(item.action)),studentEl('span',[item.name,item.email].filter(Boolean).join(' · ')||'Администратор'),studentEl('small',item.action));if(item.details?.blocked!==undefined)card.append(studentEl('span',item.details.blocked?'Ученик заблокирован':'Ученик разблокирован'));if(item.details?.status)card.append(studentEl('span',({new:'Новое',in_progress:'В работе',resolved:'Решено'})[item.details.status]||item.details.status));if(item.details?.groups)card.append(studentEl('span',`Группы: ${item.details.groups.join(', ')||'без группы'}`));if(item.entityId)card.append(studentEl('small',`ID объекта: ${item.entityId}`));$('#audit-list').append(card);});auditOffset+=data.items.length;$('#audit-more').classList.toggle('hidden',!data.hasMore);$('#audit-notice').textContent=auditOffset?'':'Действий пока нет.';}catch(e){if(generation===auditGeneration)$('#audit-notice').textContent=e.message;}finally{if(generation===auditGeneration)$('#audit-more').disabled=false;}}

function renderWeakTopicRange(root,topics){
 const panel=studentEl('section',null,'weak-topics'),heading=studentEl('h3','Слабые темы · диапазон точности'),output=studentEl('output'),slider=studentEl('div',null,'dual-range'),track=studentEl('div',null,'dual-range-track'),fill=studentEl('span'),low=studentEl('input'),high=studentEl('input'),list=studentEl('div');
 track.append(fill);[low,high].forEach(input=>{input.type='range';input.min='0';input.max='100';input.step='1';});low.value='0';high.value='70';low.setAttribute('aria-label','Минимальная точность, %');high.setAttribute('aria-label','Максимальная точность, %');slider.append(track,low,high);
 panel.append(heading,output,slider,studentEl('small','Границы включены. Минимум 5 проверенных ответов за всё время; самопроверка исключена, повторные ответы учитываются.'),list);root.append(panel);
 function update(changed){if(+low.value>+high.value){if(changed===low)low.value=high.value;else high.value=low.value;}const min=+low.value,max=+high.value;output.textContent=`Точность: ${min}–${max}%`;fill.style.left=min+'%';fill.style.width=(max-min)+'%';low.setAttribute('aria-valuetext',min+'%');high.setAttribute('aria-valuetext',max+'%');low.style.zIndex=min>90?'4':'2';list.replaceChildren();const selected=topics.filter(t=>t.answered>=5&&100*t.correct/t.answered>=min&&100*t.correct/t.answered<=max);selected.forEach(t=>list.append(studentEl('p',`${t.subject==='history'?'История':'Обществознание'} · ${t.topic}${t.taskNumber?` · задание № ${t.taskNumber}`:''}: ${t.correct} из ${t.answered} (${Math.round(t.correct/t.answered*1000)/10}%)`)));if(!selected.length)list.append(studentEl('p','Тем с достаточным количеством ответов в этом диапазоне нет.'));}
 low.oninput=()=>update(low);high.oninput=()=>update(high);update();
}

let searchStatisticsGeneration=0;
$('#search-statistics-form').onsubmit=e=>{e.preventDefault();loadSearchStatistics();};
async function loadSearchStatistics(){const generation=++searchStatisticsGeneration,start=$('#search-statistics-start'),end=$('#search-statistics-end');if(!end.value){end.value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow'}).format(new Date());const first=new Date(end.value+'T12:00:00Z');first.setUTCDate(first.getUTCDate()-29);start.value=first.toISOString().slice(0,10);}$('#search-statistics-notice').textContent='Загружаем…';try{const {data}=await api(`/admin/students/search-statistics?${new URLSearchParams({start:start.value,end:end.value})}`);if(generation!==searchStatisticsGeneration)return;const root=$('#search-statistics-items');root.replaceChildren();$('#search-statistics-notice').textContent=`Всего поисков: ${data.total}.`;data.items.forEach(item=>{const block=studentEl('details',null,'search-statistics-accordion');block.append(studentEl('summary',`${item.title} · Поисков: ${item.count}`),studentEl('p',`Поисков: ${item.count} · Посетителей: ${item.students} · Доля: ${data.total?Math.round(item.count/data.total*100):0}%`));const bar=studentEl('div',null,'timeline-bar'),fill=studentEl('span');fill.style.width=(data.total?item.count/data.total*100:0)+'%';bar.append(fill);block.append(bar,studentEl('h4','Чаще всего ищут'));item.queries.forEach(q=>block.append(studentEl('p',`${q.query} — ${q.count}`)));if(!item.queries.length)block.append(studentEl('p','Поисков пока нет.'));root.append(block);});}catch(e){if(generation===searchStatisticsGeneration)$('#search-statistics-notice').textContent=e.message;}}
