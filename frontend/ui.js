// DOM helpers and reusable components. Every piece of API data is inserted as text
// (never as HTML), so user-supplied content cannot inject markup or scripts.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") el.value = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
}

// --- Icons (static, trusted SVG markup) ------------------------------------------------

const ICONS = {
  pulse: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  pin: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  flask: '<path d="M9 3h6M10 3v6.5L4.8 18.2A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-2.8L14 9.5V3"/><path d="M7.5 15h9"/>',
  building: '<rect x="4" y="3" width="16" height="18" rx="1.5"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M10 21v-4h4v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20M6 15h4"/>',
  logout: '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v5M14 11v5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
  shield: '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3z"/><path d="m9 12 2 2 4-4"/>',
  tag: '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9-9-9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.9-3M4 5v3h3M4 13a8 8 0 0 0 14.9 3M20 19v-3h-3"/>',
  home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/>',
  arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  droplet: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11z"/>',
  hourglass: '<path d="M6 3h12M6 21h12M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9"/>',
  file: '<path d="M14 3H6v18h12V7l-4-4z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
  reschedule: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="m10 15 2 2 4-4"/>',
  userCheck: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="m16 11 2 2 4-4"/>',
  userX: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="m17 8 4 4M21 8l-4 4"/>',
  play: '<path d="M7 4v16l13-8z"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  leaf: '<path d="M5 19c0-8 5-14 15-15-1 10-7 15-15 15z"/><path d="M5 19 13 11"/>',
  heart: '<path d="M12 20s-7.5-4.6-9.3-9.3A4.8 4.8 0 0 1 12 6.6a4.8 4.8 0 0 1 9.3 4.1C19.5 15.4 12 20 12 20z"/>',
  webhook: '<circle cx="6" cy="17" r="3"/><circle cx="18" cy="17" r="3"/><circle cx="12" cy="6" r="3"/><path d="M9 17h6M7.5 14.5 10.5 8.5M16.5 14.5l-3-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
};

export function icon(name, className = "") {
  const template = document.createElement("template");
  template.innerHTML = `<svg class="icon ${className}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] ?? ""}</svg>`;
  return template.content.firstElementChild;
}

// --- Formatting ------------------------------------------------------------------------

const money = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const moneyShort = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const longDate = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });
const timeOnly = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
const weekday = new Intl.DateTimeFormat(undefined, { weekday: "short" });
const monthShort = new Intl.DateTimeFormat(undefined, { month: "short" });

export const formatMoney = (value) => money.format(Number(value)); // display only; amounts stay strings
export const formatMoneyShort = (value) => moneyShort.format(Number(value));
export const formatDateTime = (iso) => dateTime.format(new Date(iso));
export const formatDate = (iso) => dateOnly.format(new Date(iso));
export const formatLongDate = (value) => longDate.format(new Date(value));
export const formatTime = (value) => timeOnly.format(new Date(value));

export function formatDuration(ms) {
  const minutes = Math.max(0, Math.round(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60 ? `${minutes % 60} min` : ""}`.trim();
  return `${Math.round(hours / 24)} days`;
}

export function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function toLocalDateValue(date) {
  return toLocalInputValue(date).slice(0, 10);
}

export function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

// --- Components ------------------------------------------------------------------------

export const STATUS_LABELS = {
  PENDING: "Awaiting payment",
  CONFIRMED: "Confirmed",
  FAILED: "Payment failed",
  CANCELLED: "Cancelled",
  EXPIRED: "Expired",
  COMPLETED: "Completed",
  NO_SHOW: "Missed",
  SUCCESS: "Paid",
  REFUNDED: "Refunded",
  APPLIED: "Applied",
  NO_OP: "No change",
  REJECTED: "Rejected",
};

export function statusBadge(status) {
  return h("span", { class: `badge badge-${status.toLowerCase().replace("_", "-")}` }, STATUS_LABELS[status] ?? status);
}

export function eyebrow(text) {
  return h("div", { class: "eyebrow" }, h("span", { class: "eyebrow-line", "aria-hidden": "true" }), text);
}

export function pageHeader({ kicker, title, subtitle, actions = [] }) {
  return h(
    "header",
    { class: "page-header" },
    h("div", {}, kicker && eyebrow(kicker), h("h1", {}, title), subtitle && h("p", {}, subtitle)),
    actions.length ? h("div", { class: "row page-actions" }, actions) : null,
  );
}

export function sectionHeader(kicker, title, action) {
  return h("div", { class: "section-header" }, h("div", {}, kicker && eyebrow(kicker), h("h2", {}, title)), action);
}

export function field(label, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hint && h("span", { class: "field-hint" }, hint));
}

export function searchInput(placeholder, onInput) {
  const input = h("input", { class: "input", type: "search", placeholder, "aria-label": placeholder });
  input.addEventListener("input", debounce(() => onInput(input.value.trim())));
  return h("div", { class: "input-icon" }, icon("search"), input);
}

export function segmented(options, selected, onChange, label) {
  const group = h("div", { class: "segmented", role: "group", "aria-label": label });
  for (const option of options) {
    const button = h("button", { type: "button", "aria-pressed": String(option.value === selected) }, option.label);
    button.addEventListener("click", () => {
      for (const other of group.children) other.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-pressed", "true");
      onChange(option.value);
    });
    group.append(button);
  }
  return group;
}

/** Small labelled pill, e.g. "Fasting 10 h". */
export function chip(iconName, text, tone = "") {
  return h("span", { class: `chip ${tone ? `chip-${tone}` : ""}` }, iconName && icon(iconName), text);
}

/** Calendar-style tile showing the day of an appointment. */
export function dateTile(iso, tone = "") {
  const date = new Date(iso);
  return h(
    "div",
    { class: `date-tile ${tone}`, "aria-hidden": "true" },
    h("small", {}, monthShort.format(date)),
    h("strong", {}, String(date.getDate())),
    h("small", {}, weekday.format(date)),
  );
}

export function statCard({ iconName, tone = "green", value, label, hint, onClick }) {
  const tag = onClick ? "button" : "div";
  return h(
    tag,
    { class: `stat-card${onClick ? " stat-card-link" : ""}`, type: onClick ? "button" : null, onclick: onClick },
    h("span", { class: `stat-icon tone-${tone}` }, icon(iconName)),
    h("span", { class: "stat-value" }, value),
    h("span", { class: "stat-label" }, label),
    hint ? h("span", { class: "stat-hint" }, hint) : null,
    onClick ? h("span", { class: "stat-arrow" }, icon("arrowRight")) : null,
  );
}

/** Shaded callout, e.g. preparation instructions. */
export function callout(iconName, title, body, tone = "") {
  return h(
    "div",
    { class: `callout ${tone ? `callout-${tone}` : ""}` },
    h("span", { class: "callout-icon" }, icon(iconName)),
    h("div", {}, h("strong", {}, title), body && h("p", {}, body)),
  );
}

export function emptyState(iconName, title, message, action) {
  return h(
    "div",
    { class: "state" },
    h("div", { class: "state-icon" }, icon(iconName, "icon-lg")),
    h("h3", {}, title),
    message && h("p", {}, message),
    action,
  );
}

export function errorState(error, retry) {
  return h(
    "div",
    { class: "state state-error" },
    h("div", { class: "state-icon" }, icon("alert", "icon-lg")),
    h("h3", {}, "Something went wrong"),
    h("p", {}, error.message),
    retry && h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: retry }, icon("refresh"), "Try again"),
  );
}

export function skeletons(kind, count) {
  const items = Array.from({ length: count }, () => h("div", { class: `skeleton skeleton-${kind}` }));
  const layout = { card: "grid", stat: "stat-grid" }[kind] ?? "list";
  return h("div", { class: layout, "aria-busy": "true" }, items);
}

export function pager({ page, pages, total, onChange }) {
  if (pages <= 1) return null;
  return h(
    "nav",
    { class: "pager", "aria-label": "Pagination" },
    h("span", {}, `Page ${page} of ${pages} · ${total} total`),
    h(
      "div",
      { class: "pager-buttons" },
      h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: page <= 1, onclick: () => onChange(page - 1) }, "Previous"),
      h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: page >= pages, onclick: () => onChange(page + 1) }, "Next"),
    ),
  );
}

/** Run an async action with the button disabled and a spinner shown. */
export async function busy(button, action) {
  const original = [...button.childNodes];
  button.disabled = true;
  button.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), ...original.filter((n) => n.nodeType === Node.TEXT_NODE));
  try {
    return await action();
  } finally {
    button.disabled = false;
    button.replaceChildren(...original);
  }
}

export function summaryList(rows) {
  return h(
    "dl",
    { class: "summary" },
    rows.filter(Boolean).map(([label, value, total]) => h("div", { class: total ? "total" : null }, h("dt", {}, label), h("dd", {}, value))),
  );
}

// --- Toasts ----------------------------------------------------------------------------

const TOAST_ICONS = { success: "check", error: "alert", info: "info" };

export function toast(message, variant = "info") {
  const region = document.getElementById("toasts");
  const node = h("div", { class: `toast toast-${variant}` }, icon(TOAST_ICONS[variant]), h("div", {}, message));
  region.append(node);
  setTimeout(() => node.remove(), variant === "error" ? 6500 : 4500);
}

// --- Modal -----------------------------------------------------------------------------

export function openModal({ title, subtitle, kicker, body, footer, wide = false, onClose }) {
  const dialog = h("dialog", { class: `modal${wide ? " modal-wide" : ""}`, "aria-label": title });
  const close = () => dialog.close();
  append(dialog, [
    h(
      "div",
      { class: "modal-head" },
      h("div", {}, kicker && eyebrow(kicker), h("h2", {}, title), subtitle && h("p", {}, subtitle)),
      h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close", onclick: close }, icon("x")),
    ),
    h("div", { class: "modal-body" }, body),
    footer ? h("div", { class: "modal-foot" }, footer) : null,
  ]);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close(); // click on the backdrop
  });
  dialog.addEventListener("close", () => {
    dialog.remove();
    onClose?.();
  });
  document.body.append(dialog);
  dialog.showModal();
  return {
    dialog,
    close,
    setBody: (node) => dialog.querySelector(".modal-body").replaceChildren(node),
    setFooter: (nodes) => dialog.querySelector(".modal-foot")?.replaceChildren(...[nodes].flat()),
  };
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    let confirmed = false;
    const confirm = h("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, type: "button" }, confirmLabel);
    const modal = openModal({
      title,
      body: h("p", { class: "muted" }, message),
      footer: [h("button", { class: "btn btn-secondary", type: "button", onclick: () => modal.close() }, "Keep it"), confirm],
      onClose: () => resolve(confirmed),
    });
    confirm.addEventListener("click", () => {
      confirmed = true;
      modal.close();
    });
    confirm.focus();
  });
}
