const API = "/api/v1";
const SESSION_KEY = "historyTrainerSession";
const ANONYMOUS_TOKEN_KEY = "historyTrainerAnonymousToken";
const TYPE_LABELS = { text: "Краткий ответ", matching: "Соответствие", ordering: "Последовательность", multiple_choice: "Несколько ответов", self_check: "Развёрнутый ответ" };

const state = {
  config: null,
  availableCount: 0,
  attempt: null,
  token: localStorage.getItem(ANONYMOUS_TOKEN_KEY),
  currentIndex: 0,
  result: null,
  selfRatings: {},
  lastSettings: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const uuid = () => crypto.randomUUID();

let trainingLocked=false;
function showView(name) {
  if(name==='attempt' && !trainingLocked){history.pushState({trainerGuard:true},'',location.href);trainingLocked=true;}
  document.body.classList.toggle('training-locked',trainingLocked);
  document.body.classList.toggle('solving-test',name==='attempt');
  ["loading", "setup", "attempt", "result", "error"].forEach((item) => {
    $(`#${item}-view`).classList.toggle("hidden", item !== name);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 3000);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (options.idempotent) headers.set("Idempotency-Key", uuid());

  if (state.token) headers.set("X-Anonymous-Session", state.token);
  const response = await window.accountRequest(path, { ...options, headers });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.detail || data?.title || "Не удалось выполнить запрос");
  return data;
}

function selectedFilters() {
  const mode = $("input[name='training-mode']:checked").value;
  return {
    mistakesOnly: mode === "mistakes",
    reviewOnly: new URLSearchParams(location.search).get('review')==='1' && ['task','period'].includes(mode),
    taskNumbers: (mode === "task" || (mode === "mistakes" && new URLSearchParams(location.search).has("task"))) && $("#task-select").value ? [Number($("#task-select").value)] : [],
    periods: (mode === "period" || (mode === "mistakes" && new URLSearchParams(location.search).has("period"))) && $("#period-select").value ? [$("#period-select").value] : [],
    categories: ["maps", "culture"].includes(mode) ? [mode] : [],
  };
}

function renderConfig() {
  $("#total-question-count").textContent = state.config.totalQuestionCount.toLocaleString("ru-RU");
  $("#task-select").innerHTML = state.config.tasks.map((item) =>
    `<option value="${item.number}">Задание № ${item.number} — ${item.questionCount} вопросов</option>`).join("");
  $("#period-select").innerHTML = state.config.periods.map((item) =>
    `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} — ${item.questionCount}</option>`).join("");
  $$("input[name='training-mode']").forEach((input) => input.addEventListener("change", modeChanged));
  $("#task-select").addEventListener("change", filtersChanged);
  $("#period-select").addEventListener("change", filtersChanged);
  modeChanged();
  updateResumeCard();
}

function modeChanged() {
  const mode = $("input[name='training-mode']:checked").value;
  const hasDetail = mode === "task" || mode === "period";
  $("#mode-detail").classList.toggle("hidden", !hasDetail);
  $("#task-select-wrap").classList.toggle("hidden", mode !== "task");
  $("#period-select-wrap").classList.toggle("hidden", mode !== "period");
  filtersChanged();
}

let countTimer;
function filtersChanged() {
  clearTimeout(countTimer);
  countTimer = setTimeout(loadAvailableCount, 180);
  updateSelectionSummary();
}

function updateSelectionSummary() {
  const filters = selectedFilters();
  let summary = filters.mistakesOnly ? "Вопросы, где последний ответ был неверным" : filters.reviewOnly ? "Повторение темы: включая ранее решённые задания" : "Все ещё не пройденные вопросы";
  if (filters.taskNumbers.length) summary = `Задание № ${filters.taskNumbers[0]}`;
  if (filters.periods.length) summary = filters.periods[0];
  if (filters.categories[0] === "maps") summary = "Карты";
  if (filters.categories[0] === "culture") summary = "Культура";
  if(filters.mistakesOnly && (filters.taskNumbers.length||filters.periods.length))summary="Работа над ошибками · "+(filters.taskNumbers.length?"№ "+filters.taskNumbers[0]+" · ":"")+summary;
  if(filters.reviewOnly)summary="Повторение темы · "+summary;
  $("#selection-summary").textContent = summary;
}

async function loadAvailableCount() {
  const label = $("#available-count");
  label.textContent = "Проверяем количество…";
  try {
    const data = await api("/history-trainer/count", { method: "POST", body: JSON.stringify(selectedFilters()) });
    state.availableCount = data.questionCount;
    label.textContent = state.availableCount ? `${selectedFilters().mistakesOnly ? "Вопросов для повторения" : selectedFilters().reviewOnly ? "Вопросов по теме" : "Осталось новых вопросов"}: ${state.availableCount.toLocaleString("ru-RU")}` : selectedFilters().mistakesOnly ? "Ошибок для повторения нет" : selectedFilters().reviewOnly ? "В выбранной теме нет доступных вопросов" : "Все вопросы этого вида уже пройдены";
    const count = $("#question-count");
    count.max = String(Math.min(5000, Math.max(1, state.availableCount)));
    if (state.availableCount && Number(count.value) > state.availableCount) count.value = String(Math.min(10, state.availableCount));
    $("#start-button").disabled = !state.availableCount;
  } catch (error) {
    label.textContent = "Не удалось проверить количество";
    $("#start-button").disabled = false;
  }
}

async function loadConfig() {
  showView("loading");
  try {
    state.config = await api("/history-trainer/config");
    renderConfig();
    showView("setup");
    const requested=new URLSearchParams(location.search);
    const mode=requested.get('mode');
    if(['mistakes','period','task'].includes(mode))$("input[value='"+mode+"']").checked=true;
    if(requested.has('period')){const select=$('#period-select'),value=requested.get('period');if(![...select.options].some(o=>o.value===value)){const option=document.createElement('option');option.value=value;option.textContent=value;select.append(option);}select.value=value;}
    if(requested.has('task')&&[...$('#task-select').options].some(o=>o.value===requested.get('task')))$('#task-select').value=requested.get('task');
    modeChanged();
    await loadAvailableCount();
    await refreshReportBadge();
    if(requested.get("attempt"))await resumeAttempt();
  } catch (error) {
    $("#error-message").textContent = `${error.message}. Проверьте, что база тренажёра импортирована и опубликована.`;
    showView("error");
  }
}

function saveSession() {
  if (!state.attempt) return;
  if (state.token) localStorage.setItem(ANONYMOUS_TOKEN_KEY, state.token);
  localStorage.setItem(SESSION_KEY, JSON.stringify({ attemptId: state.attempt.id, token: state.token }));
}

function clearSession() {
  localStorage.removeItem(SESSION_KEY);
  updateResumeCard();
}

function storedSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); } catch { return null; }
}

function updateResumeCard() {
  $("#resume-card").classList.toggle("hidden", !storedSession());
}

async function resumeAttempt() {
  const saved = new URLSearchParams(location.search).get("attempt") ? {attemptId:new URLSearchParams(location.search).get("attempt")} : storedSession();
  if (!saved) return;
    state.token = saved.token || localStorage.getItem(ANONYMOUS_TOKEN_KEY);
  try {
    state.attempt = await api(`/attempts/${saved.attemptId}`);
    if (state.attempt.status === "completed") {
      state.result=state.attempt;renderResult();showView("result");return;
    }
    if (state.attempt.status !== "in_progress") {
      clearSession();
      toast("Предыдущая тренировка уже завершена");
      return;
    }
    const firstUnanswered = state.attempt.questions.findIndex((item) => !item.answered);
    state.currentIndex = firstUnanswered < 0 ? state.attempt.questions.length - 1 : firstUnanswered;
    showView("attempt");
    renderQuestion();
  } catch (error) {
    clearSession();
    toast("Не удалось продолжить предыдущую тренировку");
  }
}

async function startAttempt(event) {
  event.preventDefault();
  const limitEnabled = $("#limit-question-count").checked;
  const count = Math.max(1, Math.min(5000, Number($("#question-count").value) || 10, state.availableCount || 5000));
  const payload = { ...selectedFilters(), questionCount: limitEnabled ? count : null };
  const button = $("#start-button");
  button.disabled = true;
  button.textContent = "Собираем вопросы…";
  try {
    state.lastSettings = payload;
    state.selfRatings = {};
    state.result = null;
    const data = await api("/history-trainer/attempts", { method: "POST", body: JSON.stringify(payload), idempotent: true });
    state.attempt = data.attempt;
    state.token = data.anonymousSessionToken || state.token;
    state.currentIndex = 0;
    saveSession();
    showView("attempt");
    renderQuestion();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Начать тренировку";
  }
}

function renderPromptExtras(prompt) {
  const sources = prompt.sourceItems || [];
  const block = $("#source-items");
  block.classList.toggle("hidden", !sources.length);
  block.innerHTML = sources.map((item, index) => `<div><strong>${index + 1}.</strong><span>${escapeHtml(item)}</span></div>`).join("");
  const image = prompt.image?.url;
  $("#question-image-button").classList.toggle("hidden", !image);
  if (image) {
    $("#question-image").src = image;
    $("#dialog-image").src = image;
  } else {
    $("#question-image").removeAttribute("src");
    $("#dialog-image").removeAttribute("src");
  }
}

function answerFields(question) {
  const prompt = question.prompt || {};
  if (question.type === "multiple_choice") {
    return (prompt.options || []).map((item) => `<label class="choice-option"><input type="checkbox" name="option" value="${escapeHtml(item.id)}"><span>${escapeHtml(item.text)}</span></label>`).join("");
  }
  if (question.type === "matching") {
    const options = (prompt.rightItems || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.text)}</option>`).join("");
    return (prompt.leftItems || []).map((item) => `<label class="matching-row"><span>${escapeHtml(item.text)}</span><select data-left-id="${escapeHtml(item.id)}" required><option value="">Выберите соответствие</option>${options}</select></label>`).join("");
  }
  if (question.type === "ordering") {
    const items = prompt.items || [];
    const options = items.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.text)}</option>`).join("");
    return items.map((_, index) => `<label class="ordering-row"><span>${index + 1} место</span><select data-order-position="${index}" required><option value="">Выберите элемент</option>${options}</select></label>`).join("");
  }
  const placeholder = question.type === "self_check" ? "Напишите развёрнутый ответ…" : "Введите ответ…";
  return `<textarea id="text-answer" placeholder="${placeholder}" required></textarea>`;
}

function renderQuestion() {
  const question = state.attempt.questions[state.currentIndex];
  const total = state.attempt.questions.length;
  const progress = Math.round(((state.currentIndex + 1) / total) * 100);
  $("#progress-label").textContent = `Вопрос ${state.currentIndex + 1} из ${total}`;
  $("#progress-percent").textContent = `${progress}%`;
  $("#progress-bar").style.width = `${progress}%`;
  $("#question-number").textContent = `Задание №${question.prompt?.taskNumber || "—"}`;
  $("#question-type").textContent = TYPE_LABELS[question.type] || question.type;
  $("#question-text").textContent = question.prompt?.text || "Вопрос без текста";
  renderPromptExtras(question.prompt || {});
  $("#answer-fields").classList.remove("hidden");
  $("#answer-fields").innerHTML = answerFields(question);
  $("#answer-feedback").className = "feedback hidden";
  $("#answer-feedback").innerHTML = "";
  $("#report-question-error").classList.add("hidden");
  const button = $("#answer-button");
  button.textContent = "Ответить";
  button.dataset.action = "submit";
  button.disabled = false;
  if (question.answered && question.result) renderFeedback(question.result, question);
}

function collectAnswer(question) {
  if (question.type === "multiple_choice") {
    const optionIds = $$("#answer-fields input:checked").map((item) => item.value);
    if (!optionIds.length) throw new Error("Выберите хотя бы один вариант");
    return { optionIds };
  }
  if (question.type === "matching") {
    const selects = $$("#answer-fields select");
    if (selects.some((item) => !item.value)) throw new Error("Заполните все соответствия");
    if (new Set(selects.map((item) => item.value)).size !== selects.length) {
      throw new Error("Каждый вариант соответствия можно выбрать только один раз");
    }
    return { pairs: Object.fromEntries(selects.map((item) => [item.dataset.leftId, item.value])) };
  }
  if (question.type === "ordering") {
    const selects = $$("#answer-fields select");
    const itemIds = selects.map((item) => item.value);
    if (itemIds.some((item) => !item)) throw new Error("Заполните всю последовательность");
    if (new Set(itemIds).size !== itemIds.length) throw new Error("Каждый элемент можно выбрать только один раз");
    return { itemIds };
  }
  const text = $("#text-answer").value.trim();
  if (!text) throw new Error("Введите ответ");
  return { text };
}

function formatCorrectAnswer(answer, question) {
  if (!answer) return "";
  if (answer.acceptedAnswers) return answer.acceptedAnswers.join("\n");
  if (answer.optionIds) {
    const options = Object.fromEntries((question.prompt?.options || []).map((item) => [item.id, item.text]));
    return answer.optionIds.map((item) => options[item] || item).join("\n");
  }
  if (answer.pairs) {
    const left = Object.fromEntries((question.prompt?.leftItems || []).map((item) => [item.id, item.text]));
    const right = Object.fromEntries((question.prompt?.rightItems || []).map((item) => [item.id, item.text]));
    return Object.entries(answer.pairs).map(([leftId, rightId]) => `${left[leftId] || leftId} — ${right[rightId] || rightId}`).join("\n");
  }
  if (answer.itemIds) {
    const items = Object.fromEntries((question.prompt?.items || []).map((item) => [item.id, item.text]));
    return answer.itemIds.map((item) => items[item] || item).join("\n");
  }
  if (answer.modelAnswer) return answer.modelAnswer.join("\n");
  return String(answer);
}

function formatSubmittedAnswer(response, question) {
  if (!response) return "";
  if (response.text !== undefined) return String(response.text);
  if (response.optionIds) {
    const options = Object.fromEntries((question.prompt?.options || []).map((item) => [item.id, item.text]));
    return response.optionIds.map((item) => options[item] || item).join(", ");
  }
  if (response.pairs) {
    const left = Object.fromEntries((question.prompt?.leftItems || []).map((item) => [item.id, item.text]));
    const right = Object.fromEntries((question.prompt?.rightItems || []).map((item) => [item.id, item.text]));
    return Object.entries(response.pairs).map(([leftId, rightId]) => `${left[leftId] || leftId} — ${right[rightId] || rightId}`).join("\n");
  }
  if (response.itemIds) {
    const items = Object.fromEntries((question.prompt?.items || []).map((item) => [item.id, item.text]));
    return response.itemIds.map((item) => items[item] || item).join(" → ");
  }
  return String(response);
}

function renderFeedback(result, question) {
  const feedback = $("#answer-feedback");
  feedback.classList.remove("hidden", "correct", "wrong", "self-check");
  if (question.type === "self_check") {
    feedback.classList.add("self-check");
    feedback.innerHTML = `<h3>Сравните свой ответ с эталоном</h3><div class="model-answer">${escapeHtml(formatCorrectAnswer(result.correctAnswer, question)).replace(/\n/g, "<br>")}</div><div class="self-rating"><button data-rating="correct" type="button">Ответил верно</button><button data-rating="partial" type="button">Частично</button><button data-rating="wrong" type="button">Есть ошибки</button></div>`;
    $$(".self-rating button", feedback).forEach((button) => button.addEventListener("click", () => {
      state.selfRatings[question.attemptQuestionId] = button.dataset.rating;
      $$(".self-rating button", feedback).forEach((item) => item.classList.toggle("selected", item === button));
    }));
  } else if (result.isCorrect) {
    feedback.classList.add("correct");
    feedback.innerHTML = `<h3>Верно!</h3>${result.explanation?.text ? `<p>${escapeHtml(result.explanation.text)}</p>` : ""}`;
  } else {
    feedback.classList.add("wrong");
    const submitted = formatSubmittedAnswer(question.submittedResponse, question);
    feedback.innerHTML = `<h3>Ответ не совпал</h3><p>Ваш ответ:</p><div class="model-answer">${escapeHtml(submitted).replace(/\n/g, "<br>")}</div>${result.explanation?.text ? `<p><strong>Правильный ответ:</strong></p><p>${escapeHtml(result.explanation.text)}</p>` : ""}`;
  }
  $("#answer-fields").classList.add("hidden");
  const reportButton = $("#report-question-error");
  reportButton.classList.remove("hidden");
  reportButton.disabled = Boolean(question.errorReported);
  reportButton.textContent = question.errorReported
    ? "Сообщение отправлено"
    : "Сообщить об ошибке в вопросе";
  const button = $("#answer-button");
  button.textContent = state.currentIndex + 1 === state.attempt.questions.length ? "Закончить тест" : "Следующий вопрос";
  button.dataset.action = "next";
  button.disabled = false;
}

async function submitAnswer(event) {
  event.preventDefault();
  const button = $("#answer-button");
  if (button.dataset.action === "next") {
    if (state.currentIndex + 1 === state.attempt.questions.length) finishAttemptEarly();
    else {
      state.currentIndex += 1;
      $("#answer-fields").classList.remove("hidden");
      renderQuestion();
    }
    return;
  }
  const question = state.attempt.questions[state.currentIndex];
  try {
    const response = collectAnswer(question);
    button.disabled = true;
    button.textContent = "Проверяем…";
    const result = await api(`/attempts/${state.attempt.id}/answers`, {
      method: "POST",
      body: JSON.stringify({ attemptQuestionId: question.attemptQuestionId, response }),
      idempotent: true,
    });
    question.answered = true;
    question.submittedResponse = response;
    question.result = result;
    renderFeedback(result, question);
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = "Ответить";
  }
}

async function completeAttempt() {
  const button = $("#answer-button");
  if($("#finish-attempt").disabled)return;
  $("#finish-attempt").disabled=true;
  button.disabled = true;
  button.textContent = "Считаем результат…";
  try {
    state.result = await api(`/attempts/${state.attempt.id}/complete`, { method: "POST", idempotent: true });
    clearSession();
    renderResult();
    showView("result");
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = "Закончить тест";
  }finally{$("#finish-attempt").disabled=false;}
}

function renderResult() {
  if(window.mountPromotion)window.mountPromotion($("#result-view"),"result","history");
  renderAnswerComparison(state.attempt.id);
  const answers = state.attempt.questions.map((item) => item.result).filter(Boolean);
  const automatic = answers.filter((item) => item.isCorrect !== null);
  const correct = automatic.filter((item) => item.isCorrect).length;
  const wrong = automatic.length - correct;
  const selfCheck = answers.length - automatic.length;
  const percentage = automatic.length ? Math.round((correct / automatic.length) * 100) : 0;
  $("#result-score").textContent = automatic.length ? `${percentage}%` : "Готово";
  $("#result-caption").textContent = automatic.length ? "автоматически проверяемые задания" : "развёрнутые ответы проверены самостоятельно";
  $("#result-total").textContent = state.attempt.questions.length;
  $("#result-answered").textContent = answers.length;
  $("#result-correct").textContent = correct;
  $("#result-wrong").textContent = wrong;
  const ratedCorrect = Object.values(state.selfRatings).filter((item) => item === "correct").length;
  $("#result-note").textContent = selfCheck ? `Развёрнутых ответов: ${selfCheck}. Из них вы отметили как верные: ${ratedCorrect}. Результаты зарегистрированных учеников доступны в личном кабинете.` : "Результаты зарегистрированных учеников сохраняются в личном кабинете. Там можно продолжить подготовку и повторить ошибки.";
}


function finishAttemptEarly() {
  const answered = state.attempt.questions.filter((item) => item.answered).length;
  const total = state.attempt.questions.length;
  if (confirm(`Закончить тест сейчас? Отвечено ${answered} из ${total} вопросов.`)) {
    completeAttempt();
  }
}

async function repeatAttempt() {
  if (!state.lastSettings) {
    showView("setup");
    return;
  }
  const synthetic = { preventDefault() {} };
  await startAttempt(synthetic);
}

function openReportDialog() {
  const question = state.attempt?.questions?.[state.currentIndex];
  if (!question || !question.answered) {
    toast("Сначала отправьте ответ на вопрос");
    return;
  }
  $("#report-error-message").value = "";
  $("#report-error-dialog").showModal();
  $("#report-error-message").focus();
}

async function submitErrorReport(event) {
  event.preventDefault();
  const question = state.attempt?.questions?.[state.currentIndex];
  if (!question) return;
  const button = $("button[type='submit']", event.currentTarget);
  const message = $("#report-error-message").value.trim();
  if (message.length < 5) {
    toast("Опишите ошибку подробнее");
    return;
  }
  button.disabled = true;
  button.textContent = "Отправляем…";
  try {
    await api("/history-trainer/error-reports", {
      method: "POST",
      body: JSON.stringify({ attemptQuestionId: question.attemptQuestionId, message }),
    });
    question.errorReported = true;
    $("#report-error-dialog").close();
    $("#report-question-error").disabled = true;
    $("#report-question-error").textContent = "Сообщение отправлено";
    toast("Сообщение отправлено администратору");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Отправить администратору";
  }
}

async function refreshReportBadge(){window.dispatchEvent(new Event('messages-changed'));}
window.addEventListener('beforeunload',event=>{if(trainingLocked){event.preventDefault();event.returnValue='';}});
window.addEventListener('popstate',()=>{if(trainingLocked){history.pushState({trainerGuard:true},'',location.href);toast('Чтобы выйти, закончите тест и нажмите «Главный экран».');}});
document.addEventListener('click',event=>{if(trainingLocked && event.target.closest('a[href]') && !(event.target.closest('#result-view .promotion-button[target="_blank"]') && !$('#result-view').classList.contains('hidden'))){event.preventDefault();toast('Чтобы выйти, закончите тест и нажмите «Главный экран».');}},true);

$("#trainer-form").addEventListener("submit", startAttempt);
$("#answer-form").addEventListener("submit", submitAnswer);
$("#retry-button").addEventListener("click", loadConfig);
$("#resume-attempt").addEventListener("click", resumeAttempt);
$("#discard-attempt").addEventListener("click", clearSession);
$("#finish-attempt").addEventListener("click", finishAttemptEarly);
$("#repeat-attempt").addEventListener("click", repeatAttempt);
$("#report-question-error").addEventListener("click", openReportDialog);
$("#report-error-form").addEventListener("submit", submitErrorReport);
$$('.dialog-close').forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
$("#new-settings").addEventListener("click", async () => {
  trainingLocked=false;
  showView("setup");
  await Promise.all([loadAvailableCount(), refreshReportBadge()]);
});
$("#limit-question-count").addEventListener("change", (event) => {
  $("#count-control").classList.toggle("hidden", !event.target.checked);
});
$$(".count-button").forEach((button) => button.addEventListener("click", () => {
  const input = $("#question-count");
  const maximum = Number(input.max) || 5000;
  input.value = String(Math.max(1, Math.min(maximum, Number(input.value || 10) + Number(button.dataset.delta))));
}));
$("#question-count").addEventListener("change", (event) => {
  event.target.value = String(Math.max(1, Math.min(Number(event.target.max) || 5000, Number(event.target.value) || 10)));
});
$("#question-image-button").addEventListener("click", () => $("#image-dialog").showModal());
$("#close-image-dialog").addEventListener("click", () => $("#image-dialog").close());
$("#image-dialog").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });

loadConfig();
setInterval(refreshReportBadge, 60000);

async function renderAnswerComparison(attemptId){
  document.getElementById('answer-comparison')?.remove();
  const user=await window.accountIdentity();if(!user)return;
  const box=document.createElement('p');box.id='answer-comparison';box.setAttribute('role','status');$('#result-note').after(box);box.textContent='Сравниваем с прежними ответами…';
  try{const data=await api('/me/practice/comparison?'+new URLSearchParams({attemptId}));if(!box.isConnected||state.attempt.id!==attemptId)return;box.textContent=data.compared?`Повторно проверено: ${data.compared}. Прежде верно: ${data.previousCorrect}, сейчас: ${data.currentCorrect}. Исправлено ошибок: ${data.corrected}. Новых ошибок: ${data.regressed}.`:'Это первая проверенная попытка для этих заданий — сравнение появится после повторения.';}catch(e){box.textContent='Не удалось загрузить сравнение: '+e.message;}
}
