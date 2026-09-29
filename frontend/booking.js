// Booking, payment and appointment-management flows shared by patients and admins.

import { api, cached } from "./api.js";
import { isAdmin, navigate, paginatedList, state } from "./store.js";
import {
  busy,
  callout,
  chip,
  confirmDialog,
  dateTile,
  emptyState,
  errorState,
  field,
  formatDateTime,
  formatDuration,
  formatLongDate,
  formatMoney,
  formatTime,
  h,
  icon,
  openModal,
  segmented,
  skeletons,
  statusBadge,
  summaryList,
  toLocalDateValue,
  toast,
} from "./ui.js";

// --- Test details ------------------------------------------------------------------------

const SAMPLE_LABELS = {
  BLOOD: "Blood sample",
  URINE: "Urine sample",
  STOOL: "Stool sample",
  SWAB: "Swab",
  IMAGING: "Imaging",
  OTHER: "Other sample",
};

export const SAMPLE_TYPES = Object.entries(SAMPLE_LABELS).map(([value, label]) => ({ value, label }));

function formatTurnaround(hours) {
  return hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} days`;
}

/** Chips summarising what the patient should know about a test. */
export function testChips(test) {
  const chips = [];
  if (test.sample_type) chips.push(chip(test.sample_type === "IMAGING" ? "sparkle" : "droplet", SAMPLE_LABELS[test.sample_type]));
  if (test.fasting_hours) chips.push(chip("hourglass", `Fast ${test.fasting_hours} h`, "peach"));
  else if (test.fasting_hours === 0) chips.push(chip("check", "No fasting", "green"));
  if (test.report_turnaround_hours) chips.push(chip("file", `Report in ${formatTurnaround(test.report_turnaround_hours)}`, "blue"));
  return chips.length ? h("div", { class: "chips" }, chips) : null;
}

/** "Before your test" guidance, or null when the test has none. */
export function prepCallout(test) {
  const text =
    test.preparation_instructions || (test.fasting_hours ? `Fast for ${test.fasting_hours} hours before your appointment; water is usually fine.` : "");
  if (!text) return null;
  return callout(test.fasting_hours ? "hourglass" : "leaf", "Before your test", text, test.fasting_hours ? "peach" : "");
}

// --- Time picker ---------------------------------------------------------------------------

// Suggested times, in the patient's local time. The API accepts any future time; these are
// just convenient clinic-hours defaults, and "Choose an exact time" allows anything else.
const SLOT_FIRST_HOUR = 7;
const SLOT_LAST_HOUR = 19;
const SLOT_STEP_MINUTES = 30;
const TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

function parseLocalDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return year ? new Date(year, month - 1, day) : null;
}

function withTime(day, hours, minutes) {
  const date = new Date(day);
  date.setHours(hours, minutes, 0, 0);
  return date;
}

function defaultDay() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  return date;
}

/**
 * Date + time-slot picker. Taken times (from the booked-times endpoint) and past times are
 * disabled; `current` marks the booking's existing time when rescheduling.
 */
function timePicker({ centreId, testId, current }) {
  let selected = null;
  let requestId = 0;
  const initialDay = current && new Date(current) > new Date() ? new Date(current) : defaultDay();

  const dateInput = h("input", { class: "input", type: "date", required: true, min: toLocalDateValue(new Date()), value: toLocalDateValue(initialDay) });
  const heading = h("strong", {});
  const grid = h("div", { class: "slot-grid", role: "group", "aria-label": "Available times" });
  const status = h("span", { class: "field-hint" });
  const customInput = h("input", { class: "input", type: "time", step: "900" });
  const customField = field("Exact time", customInput, "Any time you like; we'll check it's free when you book.");
  customField.hidden = true;
  const toggle = h("button", { class: "link-button", type: "button" }, icon("clock"), "Choose an exact time instead");

  const select = (date) => {
    selected = date;
    for (const button of grid.children) button.setAttribute("aria-pressed", String(Number(button.dataset.time) === date?.getTime()));
  };

  const render = (day, booked) => {
    const now = Date.now();
    const currentMs = current ? new Date(current).getTime() : null;
    let free = 0;
    const buttons = [];
    for (let minutes = SLOT_FIRST_HOUR * 60; minutes < SLOT_LAST_HOUR * 60; minutes += SLOT_STEP_MINUTES) {
      const time = withTime(day, Math.floor(minutes / 60), minutes % 60);
      const ms = time.getTime();
      const isCurrent = ms === currentMs;
      const taken = booked.has(ms) && !isCurrent;
      const past = ms <= now;
      const unavailable = taken || past || isCurrent;
      if (!unavailable) free += 1;
      buttons.push(
        h(
          "button",
          {
            type: "button",
            class: `slot${isCurrent ? " current" : ""}`,
            disabled: unavailable,
            dataset: { time: String(ms) },
            "aria-pressed": String(selected?.getTime() === ms),
            title: isCurrent ? "Your current time" : taken ? "Already booked" : past ? "Already passed" : null,
            onclick: () => {
              customInput.value = "";
              select(time);
            },
          },
          formatTime(time),
        ),
      );
    }
    grid.replaceChildren(...buttons);
    status.textContent = free
      ? `${free} suggested times free · shown in your time zone (${TIME_ZONE})`
      : "No suggested times left on this day. Try another date or choose an exact time.";
  };

  const load = async () => {
    const day = parseLocalDate(dateInput.value);
    if (!day) return;
    heading.textContent = formatLongDate(day);
    const id = ++requestId;
    status.textContent = "Checking availability…";
    const end = new Date(day);
    end.setDate(end.getDate() + 1);
    let booked = new Set();
    try {
      const data = await api(`/centres/${centreId}/tests/${testId}/booked-times`, {
        query: { start: day.toISOString(), end: end.toISOString() },
      });
      booked = new Set(data.booked.map((value) => new Date(value).getTime()));
    } catch {
      /* availability is advisory; the API still rejects a taken slot */
    }
    if (id === requestId) render(day, booked);
  };

  const syncCustom = () => {
    const day = parseLocalDate(dateInput.value);
    if (!customInput.value || !day) return;
    const [hours, minutes] = customInput.value.split(":").map(Number);
    select(withTime(day, hours, minutes));
  };

  dateInput.addEventListener("change", () => {
    select(null);
    syncCustom();
    load();
  });
  customInput.addEventListener("input", syncCustom);
  toggle.addEventListener("click", () => {
    customField.hidden = !customField.hidden;
    if (!customField.hidden) customInput.focus();
  });

  load();
  return {
    node: h(
      "div",
      { class: "stack" },
      field("Date", dateInput),
      h("div", { class: "stack" }, h("div", { class: "row" }, icon("calendar"), heading), grid, status),
      h(
        "div",
        { class: "slot-legend" },
        h("span", {}, "Crossed out: taken or passed"),
        current ? h("span", {}, "Gold outline: your current time") : null,
      ),
      toggle,
      customField,
    ),
    get value() {
      return selected;
    },
  };
}

// --- Book / reschedule ----------------------------------------------------------------------

/**
 * Opens the booking dialog. With `booking`, it reschedules that booking instead.
 * `onDone(booking)` runs after a successful reschedule.
 */
export function openBookingModal({ test, centre, price, booking, onDone }) {
  const rescheduling = Boolean(booking);
  const picker = timePicker({ centreId: centre.id, testId: test.id, current: booking?.appointment_datetime });
  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h(
    "button",
    { class: "btn btn-primary", type: "button" },
    rescheduling ? icon("reschedule") : icon("calendar"),
    rescheduling ? "Move appointment" : `Reserve for ${formatMoney(price)}`,
  );

  const side = h(
    "div",
    { class: "booking-side" },
    h(
      "div",
      { class: "card card-pad stack" },
      h("div", { class: "item-card-head" }, h("span", { class: "item-icon" }, icon("flask")), h("div", {}, h("h3", {}, test.name), h("span", { class: "meta-line" }, icon("pin"), `${centre.name} · ${centre.location}`))),
      testChips(test),
    ),
    prepCallout(test),
    rescheduling
      ? callout("info", "Currently booked", formatDateTime(booking.appointment_datetime))
      : h("div", { class: "price-preview" }, h("span", {}, "Centre price"), h("strong", {}, formatMoney(price))),
    rescheduling ? null : h("p", { class: "field-hint" }, "Your slot is held while you pay. You can pay now or from My bookings."),
  );

  const modal = openModal({
    kicker: rescheduling ? "A better time for you" : "Book an appointment",
    title: rescheduling ? "Change appointment time" : "Pick a date and time",
    body: h("div", { class: "stack" }, h("div", { class: "booking-layout" }, side, picker.node), error),
    footer: [h("button", { class: "btn btn-secondary", type: "button", onclick: () => modal.close() }, rescheduling ? "Keep current time" : "Cancel"), submit],
    wide: true,
  });

  submit.addEventListener("click", async () => {
    error.textContent = "";
    const when = picker.value;
    if (!when) {
      error.textContent = "Please choose a time.";
      return;
    }
    await busy(submit, async () => {
      try {
        if (rescheduling) {
          const updated = await api(`/bookings/${booking.id}/reschedule`, { method: "PATCH", body: { appointment_datetime: when.toISOString() } });
          modal.close();
          toast(`Appointment moved to ${formatDateTime(updated.appointment_datetime)}.`, "success");
          onDone?.(updated);
        } else {
          const created = await api("/bookings/", {
            method: "POST",
            body: { test_id: test.id, centre_id: centre.id, appointment_datetime: when.toISOString() },
          });
          modal.close();
          openPaymentPrompt(created, test);
        }
      } catch (err) {
        error.textContent = err.message;
      }
    });
  });
}

/** Re-open the booking dialog for the same test and centre as an earlier booking. */
async function bookAgain(booking) {
  try {
    const [test, centre, offers] = await Promise.all([
      cached(`/tests/${booking.test_id}`),
      cached(`/centres/${booking.centre_id}`),
      api(`/tests/${booking.test_id}/centres`, { query: { page_size: 100 } }),
    ]);
    const offer = offers.items.find((item) => item.centre_id === booking.centre_id);
    if (!offer) {
      toast("This centre no longer offers this test. Find another centre in the catalogue.", "error");
      return;
    }
    openBookingModal({ test, centre, price: offer.price });
  } catch (error) {
    toast(error.message, "error");
  }
}

async function rescheduleBooking(booking, refresh) {
  try {
    const [test, centre] = await Promise.all([cached(`/tests/${booking.test_id}`), cached(`/centres/${booking.centre_id}`)]);
    openBookingModal({ test, centre, price: booking.amount, booking, onDone: refresh });
  } catch (error) {
    toast(error.message, "error");
  }
}

// --- Payment --------------------------------------------------------------------------------

function holdText(booking) {
  if (!booking.hold_expires_at) return null;
  return `We'll hold this slot for ${formatDuration(new Date(booking.hold_expires_at) - Date.now())} while you pay.`;
}

function openPaymentPrompt(booking, test) {
  const pay = h("button", { class: "btn btn-primary", type: "button" }, icon("card"), `Pay ${formatMoney(booking.amount)}`);
  const modal = openModal({
    kicker: "Almost there",
    title: "Your slot is reserved",
    body: h(
      "div",
      { class: "stack" },
      resultHero("calendar", "", "Complete payment to confirm", holdText(booking) ?? "Complete payment to confirm the appointment."),
      summaryList([
        ["Test", booking.test_name],
        ["Centre", booking.centre_name],
        ["When", formatDateTime(booking.appointment_datetime)],
        ["Amount due", formatMoney(booking.amount), true],
      ]),
      test ? prepCallout(test) : null,
    ),
    footer: [
      h("button", { class: "btn btn-secondary", type: "button", onclick: () => (modal.close(), navigate("bookings")) }, "Pay later"),
      pay,
    ],
  });
  pay.addEventListener("click", () =>
    busy(pay, async () => {
      const payment = await payBooking(booking.id);
      if (payment) {
        modal.close();
        showPaymentResult(payment);
      }
    }),
  );
}

async function payBooking(bookingId) {
  try {
    return await api("/payments/", { method: "POST", body: { booking_id: bookingId } });
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

const PAYMENT_RESULTS = {
  SUCCESS: ["check", "success", "Payment successful", "Your appointment is confirmed. We'll see you there."],
  FAILED: ["x", "danger", "Payment declined", "This booking couldn't be confirmed. Use “Book again” to reserve a new slot."],
  PENDING: ["clock", "", "Payment processing", "The provider is still processing your payment. Your booking will update once it confirms."],
};

function showPaymentResult(payment) {
  const [iconName, tone, title, message] = PAYMENT_RESULTS[payment.status];
  const done = h("button", { class: "btn btn-primary", type: "button" }, "View my bookings");
  const modal = openModal({
    title: "Payment",
    body: h(
      "div",
      { class: "stack" },
      resultHero(iconName, tone, title, message),
      summaryList([
        ["Amount", formatMoney(payment.amount)],
        ["Reference", h("span", { class: "mono" }, payment.provider_payment_id)],
        ["Booking status", statusBadge(payment.booking_status)],
      ]),
    ),
    footer: [done],
    onClose: () => navigate("bookings"),
  });
  done.addEventListener("click", () => modal.close());
}

function resultHero(iconName, tone, title, message) {
  return h(
    "div",
    { class: "result-hero" },
    h("span", { class: `item-icon ${tone}` }, icon(iconName)),
    h("h3", {}, title),
    h("p", { class: "muted" }, message),
  );
}

// --- Booking list -----------------------------------------------------------------------------

export const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "PENDING", label: "Awaiting payment" },
  { value: "CONFIRMED", label: "Confirmed" },
  { value: "COMPLETED", label: "Completed" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "EXPIRED", label: "Expired" },
  { value: "FAILED", label: "Failed" },
  { value: "NO_SHOW", label: "Missed" },
];

/** Status filter + paginated booking list. The API scopes it: patients see their own, admins see all. */
export function bookingsSection(container, { onPage } = {}) {
  const results = h("div");
  let status = "";
  const list = paginatedList(results, {
    fetchPage: (page) => api("/bookings/", { query: { page, page_size: 10, status } }),
    loading: () => skeletons("row", 4),
    onPage,
    empty: () =>
      status || isAdmin()
        ? emptyState("calendar", status ? "No bookings with this status" : "No bookings yet")
        : emptyState(
            "calendar",
            "No bookings yet",
            "Find a test and book your first appointment.",
            h("a", { class: "btn btn-primary btn-sm", href: "#/tests" }, "Find a test"),
          ),
    renderItems: (items) => h("div", { class: "list" }, items.map((booking) => bookingRow(booking, () => list.reload()))),
  });

  container.append(
    h(
      "div",
      { class: "toolbar" },
      segmented(
        STATUS_FILTERS,
        status,
        (value) => {
          status = value;
          list.reset();
        },
        "Filter by status",
      ),
    ),
    results,
  );
  list.reload();
  return list;
}

function actionButton(label, iconName, variant, onClick) {
  const button = h("button", { class: `btn btn-sm ${variant}`, type: "button" }, iconName && icon(iconName), label);
  button.addEventListener("click", () => onClick(button));
  return button;
}

/** One appointment, with the actions allowed for its status and the current user. */
export function bookingRow(booking, refresh) {
  const admin = isAdmin();
  const owner = booking.user_id === state.user.id;
  const now = Date.now();
  const upcoming = new Date(booking.appointment_datetime).getTime() > now;
  const holdLeft = booking.hold_expires_at ? new Date(booking.hold_expires_at).getTime() - now : null;
  const holdLapsed = holdLeft !== null && holdLeft <= 0;
  const canChange = admin || now < new Date(booking.changeable_until).getTime();
  const actions = [];
  let note = null;

  if (booking.status === "PENDING") {
    if (booking.payment_in_progress) {
      note = h("span", { class: "booking-note" }, "Payment processing…");
    } else if (holdLapsed) {
      note = h("span", { class: "booking-note warn" }, "Reservation lapsed");
      if (owner) actions.push(actionButton("Book again", "refresh", "btn-soft", () => bookAgain(booking)));
    } else {
      if (holdLeft !== null) note = h("span", { class: "booking-note warn" }, `Pay within ${formatDuration(holdLeft)}`);
      if (owner) {
        actions.push(
          actionButton("Pay", "card", "btn-primary", (button) =>
            busy(button, async () => {
              const payment = await payBooking(booking.id);
              if (payment) {
                refresh();
                showPaymentResult(payment);
              }
            }),
          ),
        );
      }
      actions.push(actionButton("Reschedule", "reschedule", "btn-secondary", () => rescheduleBooking(booking, refresh)));
      actions.push(actionButton("Cancel", null, "btn-danger-ghost", (button) => cancelBooking(booking, button, refresh)));
    }
  } else if (booking.status === "CONFIRMED") {
    if (upcoming && canChange) {
      actions.push(actionButton("Reschedule", "reschedule", "btn-secondary", () => rescheduleBooking(booking, refresh)));
      actions.push(actionButton(admin && !owner ? "Cancel & refund" : "Cancel", null, "btn-danger-ghost", (button) => cancelBooking(booking, button, refresh)));
      if (!admin) note = h("span", { class: "booking-note" }, `Free changes until ${formatDateTime(booking.changeable_until)}`);
    } else if (upcoming) {
      note = h("span", { class: "booking-note" }, "Changes closed · contact the centre");
    } else if (admin) {
      actions.push(actionButton("Attended", "userCheck", "btn-soft", (button) => recordAttendance(booking, "COMPLETED", button, refresh)));
      actions.push(actionButton("Missed", "userX", "btn-danger-ghost", (button) => recordAttendance(booking, "NO_SHOW", button, refresh)));
    } else {
      note = h("span", { class: "booking-note" }, "Awaiting check-in by the centre");
    }
  } else if (["FAILED", "EXPIRED", "CANCELLED"].includes(booking.status) && owner) {
    actions.push(actionButton("Book again", "refresh", "btn-soft", () => bookAgain(booking)));
  }
  actions.push(
    h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", title: "Details", "aria-label": "Booking details", onclick: () => openBookingDetails(booking, refresh) }, icon("file")),
  );

  return h(
    "article",
    { class: "card booking" },
    dateTile(booking.appointment_datetime, upcoming ? "" : "past"),
    h(
      "div",
      { class: "booking-title" },
      h("strong", {}, booking.test_name),
      h("span", { class: "meta-line" }, icon("pin"), `${booking.centre_name} · ${booking.centre_location}`),
      h("span", { class: "meta-line" }, icon("clock"), formatDateTime(booking.appointment_datetime)),
      admin ? h("span", { class: "small subtle" }, `#${booking.id} · ${booking.patient_name} (${booking.patient_email})`) : null,
    ),
    h("div", { class: "booking-meta" }, statusBadge(booking.status), h("span", { class: "booking-amount" }, formatMoney(booking.amount)), note),
    h("div", { class: "booking-actions" }, actions),
  );
}

async function cancelBooking(booking, button, refresh) {
  const refunds = booking.status === "CONFIRMED";
  const owner = booking.user_id === state.user.id;
  const what = `${booking.test_name} at ${booking.centre_name} on ${formatDateTime(booking.appointment_datetime)}${owner ? "" : ` for ${booking.patient_name}`}.`;
  const ok = await confirmDialog({
    title: refunds ? "Cancel this confirmed appointment?" : "Cancel this booking?",
    message: refunds ? `${what} The payment of ${formatMoney(booking.amount)} will be refunded in full. This can't be undone.` : `${what} This can't be undone.`,
    confirmLabel: refunds ? "Cancel & refund" : "Cancel booking",
    danger: true,
  });
  if (!ok) return;
  await busy(button, async () => {
    try {
      await api(`/bookings/${booking.id}/cancel`, { method: "PATCH" });
      toast(refunds ? `Appointment cancelled and ${formatMoney(booking.amount)} refunded.` : "Booking cancelled.", "success");
      refresh();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function recordAttendance(booking, status, button, refresh) {
  await busy(button, async () => {
    try {
      await api(`/bookings/${booking.id}/attendance`, { method: "PATCH", body: { status } });
      toast(status === "COMPLETED" ? "Marked as attended." : "Marked as missed.", "success");
      refresh();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

// --- Details --------------------------------------------------------------------------------------

const PAYMENT_ICONS = { SUCCESS: "check", FAILED: "x", PENDING: "clock", REFUNDED: "refresh" };

function paymentTimeline(payments, { onReconcile }) {
  if (!payments.length) return h("p", { class: "muted small" }, "No payment attempts yet.");
  return h(
    "div",
    { class: "timeline" },
    payments.map((payment) => {
      const reconcile =
        onReconcile && payment.status === "PENDING"
          ? h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: (event) => onReconcile(payment, event.currentTarget) }, icon("refresh"), "Reconcile")
          : null;
      return h(
        "div",
        { class: "timeline-item" },
        h("span", { class: "item-icon" }, icon(PAYMENT_ICONS[payment.status] ?? "card")),
        h(
          "div",
          { class: "grow" },
          h("strong", {}, formatMoney(payment.amount)),
          h("span", { class: "small subtle" }, `${formatDateTime(payment.created_at)} · ${payment.provider}`),
          h("span", { class: "mono subtle" }, payment.provider_payment_id),
        ),
        statusBadge(payment.status),
        reconcile,
      );
    }),
  );
}

export async function openBookingDetails(booking, refresh) {
  const admin = isAdmin();
  const modal = openModal({ kicker: `Booking #${booking.id}`, title: booking.test_name, subtitle: `${booking.centre_name} · ${booking.centre_location}`, body: skeletons("row", 2), wide: true });
  try {
    const [test, payments] = await Promise.all([cached(`/tests/${booking.test_id}`), api(`/bookings/${booking.id}/payments`)]);
    const onReconcile = admin
      ? (payment, button) =>
          busy(button, async () => {
            try {
              const result = await api(`/admin/payments/${payment.id}/reconcile`, { method: "POST" });
              toast(`Payment settled as ${result.status}. Booking is now ${result.booking_status}.`, "success");
              modal.close();
              refresh?.();
            } catch (error) {
              toast(error.message, "error");
            }
          })
      : null;
    modal.setBody(
      h(
        "div",
        { class: "split" },
        h(
          "div",
          { class: "stack" },
          summaryList([
            admin ? ["Patient", `${booking.patient_name} · ${booking.patient_email}`] : null,
            ["When", formatDateTime(booking.appointment_datetime)],
            ["Status", statusBadge(booking.status)],
            ["Booked on", formatDateTime(booking.created_at)],
            booking.hold_expires_at ? ["Hold until", formatDateTime(booking.hold_expires_at)] : null,
            ["Amount", formatMoney(booking.amount), true],
          ]),
          h("h3", { class: "small" }, "Payments"),
          paymentTimeline(payments, { onReconcile }),
        ),
        h("div", { class: "stack" }, testChips(test), prepCallout(test) ?? callout("leaf", "No special preparation", "Follow any advice from your doctor or the centre."), test.description ? h("p", { class: "muted small" }, test.description) : null),
      ),
    );
  } catch (error) {
    modal.setBody(errorState(error));
  }
}
