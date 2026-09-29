// Admin console: overview, bookings, catalogue, users and the payment webhook log.

import { api, clearCache } from "./api.js";
import { SAMPLE_TYPES, bookingsSection } from "./booking.js";
import { paginatedList, state } from "./store.js";
import {
  STATUS_LABELS,
  busy,
  callout,
  confirmDialog,
  emptyState,
  errorState,
  field,
  formatDate,
  formatDateTime,
  formatMoney,
  formatMoneyShort,
  h,
  icon,
  initials,
  openModal,
  pageHeader,
  searchInput,
  sectionHeader,
  segmented,
  skeletons,
  statCard,
  statusBadge,
  toast,
} from "./ui.js";

/** Wraps a mutating admin call: toast on success/failure, invalidate the name cache. */
async function adminAction(button, action, successMessage) {
  return busy(button, async () => {
    try {
      const result = await action();
      clearCache();
      if (successMessage) toast(successMessage, "success");
      return result ?? true;
    } catch (error) {
      toast(error.message, "error");
      return null;
    }
  });
}

function adminTable(headers, rows) {
  return h(
    "div",
    { class: "card table-wrap" },
    h("table", { class: "table" }, h("thead", {}, h("tr", {}, headers.map(([label, cls]) => h("th", { class: cls }, label)))), h("tbody", {}, rows)),
  );
}

function iconButton(name, label, onClick, danger = false) {
  return h("button", { class: `btn btn-icon btn-sm ${danger ? "btn-danger-ghost" : "btn-ghost"}`, type: "button", title: label, "aria-label": label, onclick: onClick }, icon(name));
}

/** A width set through the CSSOM (allowed by the CSP, unlike inline style attributes). */
function sized(node, percent) {
  node.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  return node;
}

// =====================================================================================
// Overview
// =====================================================================================

const PERIODS = [
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

const STATUS_ORDER = ["PENDING", "CONFIRMED", "COMPLETED", "CANCELLED", "EXPIRED", "FAILED", "NO_SHOW"];

export function viewAdminOverview(main) {
  let days = 30;
  const body = h("div");
  const run = h("button", { class: "btn btn-secondary", type: "button" }, icon("play"), "Run maintenance");
  run.addEventListener("click", async () => {
    const report = await adminAction(run, () => api("/admin/maintenance/run", { method: "POST" }));
    if (!report) return;
    const errors = report.reconcile_errors.length ? ` ${report.reconcile_errors.length} couldn't be settled.` : "";
    toast(`Expired ${report.expired_bookings} unpaid booking(s) and settled ${report.reconciled_payments} stuck payment(s).${errors}`, "success");
    load();
  });

  async function load() {
    body.replaceChildren(skeletons("stat", 4));
    try {
      renderStats(body, await api("/admin/stats", { query: { days } }));
    } catch (error) {
      body.replaceChildren(errorState(error, load));
    }
  }

  main.append(
    pageHeader({
      kicker: "Administration",
      title: "Overview",
      subtitle: "Bookings, revenue and payment health across every centre.",
      actions: [
        segmented(PERIODS, days, (value) => {
          days = value;
          load();
        }, "Reporting period"),
        run,
      ],
    }),
    body,
  );
  load();
}

function renderStats(container, stats) {
  const rate = stats.payment_success_rate === null ? "—" : `${Math.round(stats.payment_success_rate * 100)}%`;
  const totalBookings = stats.bookings_total;

  const statusBar = h(
    "div",
    { class: "bar", role: "img", "aria-label": "Bookings by status" },
    STATUS_ORDER.filter((s) => stats.bookings_by_status[s]).map((s) =>
      sized(h("span", { class: `fill-${s.toLowerCase().replace("_", "-")}`, title: `${STATUS_LABELS[s]}: ${stats.bookings_by_status[s]}` }), (stats.bookings_by_status[s] / totalBookings) * 100),
    ),
  );
  const legend = h(
    "div",
    { class: "legend" },
    STATUS_ORDER.map((s) => h("span", {}, h("i", { class: `fill-${s.toLowerCase().replace("_", "-")}` }), `${STATUS_LABELS[s]} · ${stats.bookings_by_status[s]}`)),
  );

  const maxRevenue = Math.max(...stats.centres.map((c) => Number(c.revenue)), 0);
  const centres = stats.centres.length
    ? adminTable(
        [["Centre"], ["Bookings", "num"], ["Revenue", "num"], ["Share"]],
        stats.centres.map((c) =>
          h(
            "tr",
            {},
            h("td", {}, h("strong", {}, c.centre_name), h("div", { class: "small subtle" }, c.location)),
            h("td", { class: "num" }, String(c.bookings)),
            h("td", { class: "num" }, formatMoney(c.revenue)),
            h("td", {}, h("div", { class: "meter" }, sized(h("span"), maxRevenue ? (Number(c.revenue) / maxRevenue) * 100 : 0))),
          ),
        ),
      )
    : emptyState("building", "No centres yet");

  const topTests = stats.top_tests.length
    ? h(
        "div",
        { class: "timeline" },
        stats.top_tests.map((t, index) =>
          h("div", { class: "timeline-item" }, h("span", { class: "item-icon" }, String(index + 1)), h("div", { class: "grow" }, h("strong", {}, t.test_name)), h("span", { class: "badge" }, `${t.bookings} booked`)),
        ),
      )
    : h("p", { class: "muted small" }, "No bookings in this period.");

  const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  const sections = [
    h(
      "div",
      { class: "stat-grid" },
      statCard({ iconName: "calendar", value: String(totalBookings), label: "Bookings", hint: `last ${stats.days} days` }),
      statCard({ iconName: "receipt", tone: "peach", value: formatMoneyShort(stats.revenue), label: "Revenue", hint: `${formatMoneyShort(stats.refunded)} refunded` }),
      statCard({ iconName: "check", tone: "blue", value: rate, label: "Payment success", hint: `${stats.payments_succeeded} paid · ${stats.payments_failed} failed` }),
      statCard({ iconName: "clock", value: String(stats.upcoming_confirmed), label: "Upcoming visits", hint: `${plural(stats.patients, "patient")} registered` }),
    ),
    stats.payments_pending
      ? h("div", { class: "section-gap" }, callout("alert", `${stats.payments_pending} payment(s) awaiting the provider`, "They settle when the webhook arrives. Ones stuck longer than the timeout are settled by the maintenance job, or from a booking's details.", "peach"))
      : null,
    h(
      "div",
      { class: "split" },
      h(
        "div",
        {},
        sectionHeader("Bookings", "By status"),
        h("div", { class: "card card-pad" }, totalBookings ? statusBar : h("p", { class: "muted small" }, "No bookings in this period."), legend),
        sectionHeader("Centres", "Revenue by centre"),
        centres,
      ),
      h(
        "div",
        {},
        sectionHeader("Demand", "Most booked tests"),
        h("div", { class: "card card-pad" }, topTests),
        sectionHeader("Automation", "Housekeeping"),
        h(
          "div",
          { class: "quote-card" },
          icon("sparkle"),
          h("p", {}, "Unpaid bookings release their slot when the hold runs out, and payments stuck at the provider are settled automatically. Run it now with “Run maintenance”."),
          h("div", { class: "eyebrow" }, "Runs every minute"),
        ),
      ),
    ),
  ];
  container.replaceChildren(...sections.filter(Boolean));
}

// =====================================================================================
// Bookings
// =====================================================================================

export function viewAdminBookings(main) {
  main.append(
    pageHeader({
      kicker: "Administration",
      title: "All bookings",
      subtitle: "Every patient's appointments. Reschedule, cancel with a refund, record attendance, and check payments.",
    }),
  );
  bookingsSection(main);
}

// =====================================================================================
// Catalogue: centres, tests, pricing
// =====================================================================================

const CATALOGUE_TABS = [
  { value: "centres", label: "Centres" },
  { value: "tests", label: "Tests" },
  { value: "pricing", label: "Pricing" },
];
let catalogueTab = "centres";

export function viewCatalogue(main) {
  const panel = h("div");
  const show = (tab) => {
    catalogueTab = tab;
    panel.replaceChildren();
    ({ centres: adminCentres, tests: adminTests, pricing: adminPricing })[tab](panel);
  };
  main.append(
    pageHeader({ kicker: "Administration", title: "Catalogue", subtitle: "Centres, the tests they offer, what patients need to know, and centre-specific pricing." }),
    h("div", { class: "toolbar" }, segmented(CATALOGUE_TABS, catalogueTab, show, "Catalogue section")),
    panel,
  );
  show(catalogueTab);
}

async function deleteResource(button, path, label, onDeleted) {
  const ok = await confirmDialog({
    title: `Delete ${label}?`,
    message: "This permanently removes it and its price list. Items with existing bookings can't be deleted.",
    confirmLabel: "Delete",
    danger: true,
  });
  if (ok && (await adminAction(button, () => api(path, { method: "DELETE" }), `${label} deleted.`))) onDeleted();
}

// --- Centres ---------------------------------------------------------------------------

function adminCentres(panel) {
  const tableArea = h("div");
  const list = paginatedList(tableArea, {
    fetchPage: (page) => api("/centres/", { query: { page, page_size: 20 } }),
    loading: () => skeletons("row", 3),
    empty: () => emptyState("building", "No centres yet", "Add your first centre above."),
    renderItems: (items) =>
      adminTable(
        [["Name"], ["Location"], ["Updated"], ["", "actions"]],
        items.map((centre) =>
          h(
            "tr",
            {},
            h("td", {}, h("strong", {}, centre.name)),
            h("td", {}, centre.location),
            h("td", { class: "muted" }, formatDate(centre.updated_at)),
            h(
              "td",
              { class: "actions" },
              iconButton("edit", `Edit ${centre.name}`, () => editCentre(centre, list.reload)),
              iconButton("trash", `Delete ${centre.name}`, (event) => deleteResource(event.currentTarget, `/centres/${centre.id}`, centre.name, list.reload), true),
            ),
          ),
        ),
      ),
  });

  const name = h("input", { class: "input", required: true, maxlength: "200", placeholder: "Apollo Diagnostics" });
  const locationInput = h("input", { class: "input", required: true, maxlength: "200", placeholder: "Delhi" });
  const add = h("button", { class: "btn btn-primary", type: "submit" }, icon("plus"), "Add centre");
  const form = h("form", { class: "card card-pad inline-form" }, field("Name", name), field("Location", locationInput), add);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const created = await adminAction(add, () => api("/centres/", { method: "POST", body: { name: name.value, location: locationInput.value } }), "Centre added.");
    if (created) {
      form.reset();
      list.reset();
    }
  });

  panel.append(h("div", { class: "stack" }, form, tableArea));
  list.reload();
}

function editCentre(centre, onSaved) {
  const name = h("input", { class: "input", value: centre.name, maxlength: "200" });
  const locationInput = h("input", { class: "input", value: centre.location, maxlength: "200" });
  const save = h("button", { class: "btn btn-primary", type: "button" }, "Save changes");
  const modal = openModal({
    title: "Edit centre",
    body: h("div", { class: "stack" }, field("Name", name), field("Location", locationInput)),
    footer: [h("button", { class: "btn btn-secondary", type: "button", onclick: () => modal.close() }, "Cancel"), save],
  });
  save.addEventListener("click", async () => {
    const ok = await adminAction(save, () => api(`/centres/${centre.id}`, { method: "PATCH", body: { name: name.value, location: locationInput.value } }), "Centre updated.");
    if (ok) {
      modal.close();
      onSaved();
    }
  });
}

// --- Tests -----------------------------------------------------------------------------

/** The editable fields of a test, shared by the add form and the edit dialog. */
function testFields(test = {}) {
  const controls = {
    name: h("input", { class: "input", required: true, maxlength: "200", placeholder: "Complete Blood Count", value: test.name ?? "" }),
    base_price: h("input", { class: "input", required: true, type: "number", min: "0.01", step: "0.01", placeholder: "500.00", value: test.base_price ?? "" }),
    sample_type: h(
      "select",
      { class: "select" },
      h("option", { value: "" }, "Not specified"),
      SAMPLE_TYPES.map((type) => h("option", { value: type.value, selected: test.sample_type === type.value }, type.label)),
    ),
    fasting_hours: h("input", { class: "input", type: "number", min: "0", max: "72", step: "1", placeholder: "0 = no fasting", value: test.fasting_hours ?? "" }),
    report_turnaround_hours: h("input", { class: "input", type: "number", min: "1", max: "720", step: "1", placeholder: "e.g. 24", value: test.report_turnaround_hours ?? "" }),
    description: h("textarea", { class: "textarea", maxlength: "2000", placeholder: "What the test measures" }, test.description ?? ""),
    preparation_instructions: h("textarea", { class: "textarea", maxlength: "2000", placeholder: "e.g. Fast for 10 hours; water is fine." }, test.preparation_instructions ?? ""),
  };
  const grid = h(
    "div",
    { class: "form-grid" },
    field("Name", controls.name),
    field("List price (₹)", controls.base_price, "Centres can set their own price under Pricing."),
    field("Sample", controls.sample_type),
    field("Fasting (hours)", controls.fasting_hours),
    field("Report ready in (hours)", controls.report_turnaround_hours),
    h("div", {}),
    h("div", { class: "span-2" }, field("Description", controls.description)),
    h("div", { class: "span-2" }, field("Preparation for patients", controls.preparation_instructions, "Shown when patients book and in their booking details.")),
  );
  const optionalNumber = (input) => (input.value === "" ? null : Number(input.value));
  const optionalText = (input) => input.value.trim() || null;
  const values = () => ({
    name: controls.name.value,
    base_price: controls.base_price.value,
    description: optionalText(controls.description),
    sample_type: controls.sample_type.value || null,
    fasting_hours: optionalNumber(controls.fasting_hours),
    report_turnaround_hours: optionalNumber(controls.report_turnaround_hours),
    preparation_instructions: optionalText(controls.preparation_instructions),
  });
  return { grid, values };
}

function adminTests(panel) {
  const tableArea = h("div");
  const list = paginatedList(tableArea, {
    fetchPage: (page) => api("/tests/", { query: { page, page_size: 20 } }),
    loading: () => skeletons("row", 3),
    empty: () => emptyState("flask", "No tests yet", "Add your first diagnostic test above."),
    renderItems: (items) =>
      adminTable(
        [["Test"], ["Sample"], ["Fasting", "num"], ["List price", "num"], ["", "actions"]],
        items.map((test) =>
          h(
            "tr",
            {},
            h("td", {}, h("strong", {}, test.name), test.description ? h("div", { class: "small subtle" }, test.description) : null),
            h("td", { class: "muted" }, SAMPLE_TYPES.find((t) => t.value === test.sample_type)?.label ?? "—"),
            h("td", { class: "num muted" }, test.fasting_hours === null ? "—" : test.fasting_hours ? `${test.fasting_hours} h` : "None"),
            h("td", { class: "num" }, formatMoney(test.base_price)),
            h(
              "td",
              { class: "actions" },
              iconButton("edit", `Edit ${test.name}`, () => editTest(test, list.reload)),
              iconButton("trash", `Delete ${test.name}`, (event) => deleteResource(event.currentTarget, `/tests/${test.id}`, test.name, list.reload), true),
            ),
          ),
        ),
      ),
  });

  const { grid, values } = testFields();
  const add = h("button", { class: "btn btn-primary", type: "submit" }, icon("plus"), "Add test");
  const form = h("form", { class: "card card-pad stack" }, h("h2", {}, "Add a test"), grid, h("div", {}, add));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (await adminAction(add, () => api("/tests/", { method: "POST", body: values() }), "Test added.")) {
      form.reset();
      list.reset();
    }
  });

  panel.append(h("div", { class: "stack" }, form, tableArea));
  list.reload();
}

function editTest(test, onSaved) {
  const { grid, values } = testFields(test);
  const save = h("button", { class: "btn btn-primary", type: "button" }, "Save changes");
  const modal = openModal({
    title: "Edit test",
    body: grid,
    wide: true,
    footer: [h("button", { class: "btn btn-secondary", type: "button", onclick: () => modal.close() }, "Cancel"), save],
  });
  save.addEventListener("click", async () => {
    const body = { ...values(), description: values().description ?? "" };
    if (await adminAction(save, () => api(`/tests/${test.id}`, { method: "PATCH", body }), "Test updated.")) {
      modal.close();
      onSaved();
    }
  });
}

// --- Pricing (centre <-> test offerings) -------------------------------------------------

async function adminPricing(panel) {
  panel.replaceChildren(skeletons("row", 2));
  let centres;
  let tests;
  try {
    [centres, tests] = await Promise.all([api("/centres/", { query: { page_size: 100 } }), api("/tests/", { query: { page_size: 100 } })]);
  } catch (error) {
    panel.replaceChildren(errorState(error, () => adminPricing(panel)));
    return;
  }
  if (!centres.items.length || !tests.items.length) {
    panel.replaceChildren(emptyState("tag", "Nothing to price yet", "Create at least one centre and one test first."));
    return;
  }

  const select = h("select", { class: "select" }, centres.items.map((centre) => h("option", { value: String(centre.id) }, `${centre.name} — ${centre.location}`)));
  const offerings = h("div");
  select.addEventListener("change", () => loadOfferings(Number(select.value)));

  async function loadOfferings(centreId) {
    offerings.replaceChildren(skeletons("row", 2));
    try {
      const data = await api(`/centres/${centreId}/tests`, { query: { page_size: 100 } });
      offerings.replaceChildren(pricingEditor(centreId, data.items, tests.items, () => loadOfferings(centreId)));
    } catch (error) {
      offerings.replaceChildren(errorState(error, () => loadOfferings(centreId)));
    }
  }

  panel.replaceChildren(
    h("div", { class: "stack" }, h("div", { class: "card card-pad" }, field("Centre", select, "Each centre sets its own price per test; bookings keep the price at the time of booking.")), offerings),
  );
  loadOfferings(Number(select.value));
}

function pricingEditor(centreId, offerings, allTests, reload) {
  const offered = new Set(offerings.map((o) => o.test_id));
  const available = allTests.filter((test) => !offered.has(test.id));

  const rows = offerings.map((offer) => {
    const base = allTests.find((t) => t.id === offer.test_id)?.base_price;
    const price = h("input", { class: "input", type: "number", min: "0.01", step: "0.01", value: offer.price, "aria-label": `Price for ${offer.test_name}` });
    const save = h("button", { class: "btn btn-secondary btn-sm", type: "button" }, "Save");
    save.addEventListener("click", async () => {
      if (await adminAction(save, () => api(`/centres/${centreId}/tests/${offer.test_id}`, { method: "PATCH", body: { price: price.value } }), `Price for ${offer.test_name} updated.`)) reload();
    });
    return h(
      "tr",
      {},
      h("td", {}, h("strong", {}, offer.test_name)),
      h("td", { class: "num muted" }, base ? formatMoney(base) : "—"),
      h("td", { class: "num" }, h("div", { class: "row price-edit" }, price, save)),
      h(
        "td",
        { class: "actions" },
        iconButton(
          "trash",
          `Stop offering ${offer.test_name}`,
          async (event) => {
            const button = event.currentTarget;
            const ok = await confirmDialog({
              title: `Stop offering ${offer.test_name}?`,
              message: "Patients will no longer be able to book it here. Existing bookings are not affected.",
              confirmLabel: "Remove",
              danger: true,
            });
            if (ok && (await adminAction(button, () => api(`/centres/${centreId}/tests/${offer.test_id}`, { method: "DELETE" }), "Test removed from centre."))) reload();
          },
          true,
        ),
      ),
    );
  });

  const table = offerings.length
    ? adminTable([["Test"], ["List price", "num"], ["Centre price (₹)", "num"], ["", "actions"]], rows)
    : emptyState("tag", "No tests offered yet", "Add a test below to start taking bookings at this centre.");

  if (!available.length) return h("div", { class: "stack" }, table);

  const testSelect = h("select", { class: "select" }, available.map((test) => h("option", { value: String(test.id) }, test.name)));
  const priceInput = h("input", { class: "input", type: "number", min: "0.01", step: "0.01" });
  const syncPlaceholder = () => {
    priceInput.placeholder = available.find((t) => t.id === Number(testSelect.value))?.base_price ?? "";
  };
  testSelect.addEventListener("change", syncPlaceholder);
  syncPlaceholder();

  const add = h("button", { class: "btn btn-primary", type: "submit" }, icon("plus"), "Offer test");
  const form = h("form", { class: "card card-pad inline-form" }, field("Test", testSelect), field("Centre price (₹)", priceInput, "Leave empty to use the list price."), add);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = { test_id: Number(testSelect.value), ...(priceInput.value ? { price: priceInput.value } : {}) };
    if (await adminAction(add, () => api(`/centres/${centreId}/tests`, { method: "POST", body }), "Test added to centre.")) reload();
  });

  return h("div", { class: "stack" }, sectionHeader(null, "Offered tests"), table, form);
}

// =====================================================================================
// Users
// =====================================================================================

const ROLE_FILTERS = [
  { value: "", label: "Everyone" },
  { value: "USER", label: "Patients" },
  { value: "ADMIN", label: "Admins" },
];

export function viewUsers(main) {
  const results = h("div");
  const filters = { search: "", role: "" };
  const list = paginatedList(results, {
    fetchPage: (page) => api("/admin/users", { query: { page, page_size: 20, ...filters } }),
    loading: () => skeletons("row", 4),
    empty: () => emptyState("users", "No matching users", "Try a different search."),
    renderItems: (items) =>
      adminTable(
        [["User"], ["Role"], ["Bookings", "num"], ["Joined"], ["Status"], ["", "actions"]],
        items.map((user) => userRow(user, () => list.reload())),
      ),
  });

  main.append(
    pageHeader({ kicker: "Administration", title: "Users", subtitle: "Deactivate accounts (they're signed out immediately) and manage who can administer the service." }),
    h(
      "div",
      { class: "toolbar" },
      searchInput("Search by name or email", (value) => {
        filters.search = value;
        list.reset();
      }),
      segmented(ROLE_FILTERS, "", (value) => {
        filters.role = value;
        list.reset();
      }, "Filter by role"),
    ),
    results,
  );
  list.reload();
}

function userRow(user, reload) {
  const self = user.id === state.user.id;
  const update = async (button, body, message) => {
    if (await adminAction(button, () => api(`/admin/users/${user.id}`, { method: "PATCH", body }), message)) reload();
  };
  const toggleRole = h(
    "button",
    { class: "btn btn-secondary btn-sm", type: "button", disabled: self },
    user.role === "ADMIN" ? "Make patient" : "Make admin",
  );
  toggleRole.addEventListener("click", async () => {
    const promote = user.role !== "ADMIN";
    const ok = await confirmDialog({
      title: promote ? `Give ${user.name} admin access?` : `Remove ${user.name}'s admin access?`,
      message: promote ? "Admins can see every patient's bookings, change the catalogue and manage users." : "They'll keep their account but lose access to the admin console.",
      confirmLabel: promote ? "Make admin" : "Remove admin",
      danger: !promote,
    });
    if (ok) update(toggleRole, { role: promote ? "ADMIN" : "USER" }, promote ? `${user.name} is now an admin.` : `${user.name} is now a patient.`);
  });
  const toggleActive = h(
    "button",
    { class: `btn btn-sm ${user.is_active ? "btn-danger-ghost" : "btn-soft"}`, type: "button", disabled: self },
    icon(user.is_active ? "userX" : "userCheck"),
    user.is_active ? "Deactivate" : "Reactivate",
  );
  toggleActive.addEventListener("click", async () => {
    if (user.is_active) {
      const ok = await confirmDialog({
        title: `Deactivate ${user.name}?`,
        message: "They'll be signed out and unable to sign in until reactivated. Their bookings are kept.",
        confirmLabel: "Deactivate",
        danger: true,
      });
      if (!ok) return;
    }
    update(toggleActive, { is_active: !user.is_active }, user.is_active ? `${user.name} deactivated.` : `${user.name} reactivated.`);
  });

  return h(
    "tr",
    {},
    h("td", {}, h("div", { class: "row" }, h("span", { class: "avatar" }, initials(user.name)), h("div", { class: "user-meta" }, h("strong", {}, user.name, self ? " (you)" : ""), h("span", {}, user.email)))),
    h("td", {}, h("span", { class: `badge ${user.role === "ADMIN" ? "badge-completed" : ""}` }, user.role === "ADMIN" ? "Admin" : "Patient")),
    h("td", { class: "num" }, String(user.booking_count)),
    h("td", { class: "muted" }, formatDate(user.created_at)),
    h("td", {}, h("span", { class: `badge ${user.is_active ? "badge-confirmed" : "badge-failed"}` }, user.is_active ? "Active" : "Deactivated")),
    h("td", { class: "actions" }, h("div", { class: "row price-edit" }, toggleRole, toggleActive)),
  );
}

// =====================================================================================
// Payment webhooks
// =====================================================================================

const OUTCOME_FILTERS = [
  { value: "", label: "All events" },
  { value: "APPLIED", label: "Applied" },
  { value: "NO_OP", label: "No change" },
  { value: "REJECTED", label: "Rejected" },
];

export function viewWebhooks(main) {
  const results = h("div");
  const filters = { outcome: "", search: "" };
  const list = paginatedList(results, {
    fetchPage: (page) => api("/admin/webhook-events", { query: { page, page_size: 20, ...filters } }),
    loading: () => skeletons("row", 4),
    empty: () => emptyState("webhook", filters.outcome || filters.search ? "No matching events" : "No webhook events yet", "Events from the payment provider appear here as they arrive."),
    renderItems: (items) =>
      adminTable(
        [["Received"], ["Event"], ["Reported"], ["Outcome"], ["", "actions"]],
        items.map((event) =>
          h(
            "tr",
            {},
            h("td", { class: "muted" }, formatDateTime(event.received_at)),
            h("td", {}, h("div", { class: "mono" }, event.event_id), h("div", { class: "small subtle" }, `Booking #${event.booking_id} · ${event.provider_payment_id}`)),
            h("td", {}, h("div", { class: "row" }, statusBadge(event.reported_status), h("span", { class: "small" }, formatMoney(event.reported_amount)))),
            h("td", {}, event.outcome ? statusBadge(event.outcome) : "—", event.outcome_detail ? h("div", { class: "small subtle" }, event.outcome_detail) : null),
            h("td", { class: "actions" }, iconButton("file", "View payload", () => showPayload(event))),
          ),
        ),
      ),
  });

  main.append(
    pageHeader({
      kicker: "Administration",
      title: "Payment webhooks",
      subtitle: "Every signed callback from the payment provider and what it did. Rejected events need a follow-up: the provider must resend corrections under a new event id.",
    }),
    h(
      "div",
      { class: "toolbar" },
      searchInput("Search event or payment id", (value) => {
        filters.search = value;
        list.reset();
      }),
      segmented(OUTCOME_FILTERS, "", (value) => {
        filters.outcome = value;
        list.reset();
      }, "Filter by outcome"),
    ),
    results,
  );
  list.reload();
}

function showPayload(event) {
  openModal({
    kicker: "Webhook event",
    title: event.event_id,
    subtitle: `Received ${formatDateTime(event.received_at)}${event.processed_at ? ` · processed ${formatDateTime(event.processed_at)}` : ""}`,
    body: h(
      "div",
      { class: "stack" },
      event.outcome === "REJECTED" ? callout("alert", "Rejected", event.outcome_detail, "red") : null,
      h("pre", { class: "payload" }, JSON.stringify(event.payload, null, 2)),
    ),
    wide: true,
  });
}
