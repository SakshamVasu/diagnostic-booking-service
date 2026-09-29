import { viewAdminBookings, viewAdminOverview, viewCatalogue, viewUsers, viewWebhooks } from "./admin.js";
import { ApiError, api, cached, clearCache, session } from "./api.js";
import { bookingRow, bookingsSection, openBookingModal, prepCallout, testChips } from "./booking.js";
import { isAdmin, navigate, paginatedList, state } from "./store.js";
import {
  busy,
  emptyState,
  errorState,
  eyebrow,
  field,
  formatDate,
  formatDateTime,
  formatMoney,
  h,
  icon,
  initials,
  openModal,
  pageHeader,
  searchInput,
  sectionHeader,
  skeletons,
  statCard,
  toast,
} from "./ui.js";

const root = document.getElementById("app");

// =====================================================================================
// Routing
// =====================================================================================

const PATIENT_ROUTES = {
  home: { label: "Overview", icon: "home", view: viewHome },
  tests: { label: "Find a test", icon: "flask", view: viewTests },
  centres: { label: "Centres", icon: "building", view: viewCentres },
  bookings: { label: "My bookings", icon: "calendar", view: viewBookings },
};

const ADMIN_ROUTES = {
  admin: { label: "Overview", icon: "chart", view: viewAdminOverview },
  "admin-bookings": { label: "Bookings", icon: "calendar", view: viewAdminBookings },
  catalogue: { label: "Catalogue", icon: "flask", view: viewCatalogue },
  users: { label: "Users", icon: "users", view: viewUsers },
  webhooks: { label: "Payment webhooks", icon: "webhook", view: viewWebhooks },
};

// Links that belong to the other role are redirected to the closest equivalent.
const ALIASES = { bookings: "admin-bookings", home: "admin", tests: "catalogue", centres: "catalogue" };

const routes = () => (isAdmin() ? ADMIN_ROUTES : PATIENT_ROUTES);

function currentRoute() {
  const name = location.hash.replace(/^#\/?/, "").split("/")[0];
  const available = routes();
  if (available[name]) return name;
  if (isAdmin() && available[ALIASES[name]]) return ALIASES[name];
  return Object.keys(available)[0];
}

async function boot() {
  window.addEventListener("hashchange", render);
  window.addEventListener("auth:expired", () => {
    state.user = null;
    toast("Your session has ended. Please sign in again.", "error");
    render();
  });
  if (session.token) {
    try {
      state.user = await api("/auth/me");
    } catch {
      session.clear();
    }
  }
  render();
}

function render() {
  if (!state.user) {
    root.replaceChildren(authView());
    return;
  }
  const routeName = currentRoute();
  const main = h("main", { class: "page", id: "main" });
  root.replaceChildren(h("div", { class: "shell" }, sidebar(routeName), h("div", { class: "content" }, topbar(routeName), main, footer())));
  routes()[routeName].view(main);
  window.scrollTo({ top: 0 });
}

function signOut() {
  session.clear();
  clearCache();
  state.user = null;
  history.replaceState(null, "", location.pathname);
  render();
}

// =====================================================================================
// Theme
// =====================================================================================

function effectiveTheme() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function themeToggle() {
  const button = h("button", { class: "btn btn-ghost btn-icon", type: "button" });
  const paint = () => {
    const dark = effectiveTheme() === "dark";
    button.replaceChildren(icon(dark ? "sun" : "moon"));
    button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    button.title = button.getAttribute("aria-label");
  };
  button.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("dbs.theme", next);
    } catch {
      /* preference not persisted */
    }
    paint();
  });
  paint();
  return button;
}

// =====================================================================================
// Shell
// =====================================================================================

function brand() {
  return h(
    "a",
    { class: "brand", href: "#/", "aria-label": "Diagnostic Booking home" },
    h("span", { class: "brand-mark" }, icon("pulse")),
    h("span", {}, "diagnostic", h("span", { class: "brand-light" }, "booking")),
  );
}

function navLinks(active) {
  return h(
    "nav",
    { class: "side-nav", "aria-label": "Main" },
    Object.entries(routes()).map(([name, route]) =>
      h("a", { href: `#/${name}`, "aria-current": name === active ? "page" : null }, icon(route.icon), route.label),
    ),
  );
}

function signOutButton() {
  return h("button", { class: "btn btn-ghost btn-icon", type: "button", title: "Sign out", "aria-label": "Sign out", onclick: signOut }, icon("logout"));
}

function sidebar(active) {
  const admin = isAdmin();
  return h(
    "aside",
    { class: "sidebar" },
    brand(),
    eyebrow(admin ? "Administration" : "Your health"),
    navLinks(active),
    h(
      "div",
      { class: "side-bottom" },
      h(
        "div",
        { class: "care-card" },
        icon(admin ? "shield" : "heart"),
        h("strong", {}, admin ? "Every payment is traceable" : "Preparing for a test?"),
        h("p", {}, admin ? "Webhooks are signed and logged, and stuck payments settle automatically." : "Each booking shows what to do beforehand, like fasting."),
      ),
      h(
        "div",
        { class: "user-row" },
        h("span", { class: "avatar", "aria-hidden": "true" }, initials(state.user.name)),
        h("div", { class: "user-meta" }, h("strong", {}, state.user.name), h("span", {}, admin ? "Administrator" : state.user.email)),
        themeToggle(),
        signOutButton(),
      ),
    ),
  );
}

/** Compact header with the same navigation, shown instead of the sidebar on small screens. */
function topbar(active) {
  return h(
    "header",
    { class: "topbar" },
    h("div", { class: "topbar-row" }, brand(), h("div", { class: "row" }, themeToggle(), signOutButton())),
    navLinks(active),
  );
}

function footer() {
  return h(
    "footer",
    { class: "page-footer" },
    h("span", {}, `© ${new Date().getFullYear()} Diagnostic Booking · simulated payments, no real charges`),
    h("a", { href: "/docs", target: "_blank", rel: "noreferrer" }, "API documentation"),
  );
}

// =====================================================================================
// Authentication
// =====================================================================================

function authView() {
  let mode = "login";
  const panel = h("section", { class: "auth-panel", "aria-label": "Sign in or create an account" });

  const paint = () => {
    const isLogin = mode === "login";
    const error = h("div", { class: "form-error", role: "alert" });
    const name = h("input", { class: "input", name: "name", autocomplete: "name", required: true, maxlength: "100", placeholder: "Your name" });
    const email = h("input", { class: "input", name: "email", type: "email", autocomplete: "email", required: true, placeholder: "you@example.com" });
    const password = h("input", {
      class: "input",
      name: "password",
      type: "password",
      required: true,
      minlength: isLogin ? null : "8",
      placeholder: isLogin ? "Your password" : "At least 8 characters",
      autocomplete: isLogin ? "current-password" : "new-password",
    });
    const submit = h("button", { class: "btn btn-primary btn-block", type: "submit" }, isLogin ? "Sign in" : "Create account", icon("arrowRight"));

    const form = h(
      "form",
      { novalidate: true },
      isLogin ? null : field("Full name", name),
      field("Email address", email),
      field("Password", password, isLogin ? null : "Avoid common passwords."),
      error,
      submit,
      h("div", { class: "form-note" }, icon("lock"), "Your health information stays private: only you and the clinic team see your bookings."),
    );

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.textContent = "";
      await busy(submit, async () => {
        try {
          if (!isLogin) {
            await api("/auth/signup", { method: "POST", body: { name: name.value, email: email.value, password: password.value } });
          }
          const token = await api("/auth/login", { method: "POST", body: { email: email.value, password: password.value } });
          session.token = token.access_token;
          state.user = await api("/auth/me");
          toast(isLogin ? `Welcome back, ${firstName()}.` : `Account created. Welcome, ${firstName()}!`, "success");
          render();
        } catch (err) {
          error.textContent = err.message;
        }
      });
    });

    const tab = (value, label) =>
      h(
        "button",
        {
          type: "button",
          role: "tab",
          "aria-selected": String(mode === value),
          onclick: () => {
            mode = value;
            paint();
          },
        },
        label,
      );

    panel.replaceChildren(
      h("div", { class: "auth-tabs", role: "tablist" }, tab("login", "Sign in"), tab("signup", "Create account")),
      h("h2", {}, isLogin ? "Welcome back" : "Let's get you started"),
      h("p", { class: "muted" }, isLogin ? "Sign in to book and manage your tests." : "Create your account in under a minute."),
      form,
    );
    (isLogin ? email : name).focus();
  };

  const point = (text) => h("li", {}, h("span", { class: "tick" }, icon("check")), text);
  const view = h(
    "div",
    { class: "auth" },
    h(
      "section",
      { class: "auth-copy" },
      h("div", { class: "auth-top" }, brand(), themeToggle()),
      eyebrow("Diagnostics, simplified"),
      h("h1", {}, "Your health checks, ", h("em", {}, "made simple.")),
      h("p", { class: "lead" }, "Compare what each diagnostic centre charges for the same test, pick a time that suits you, and know exactly how to prepare."),
      h(
        "ul",
        { class: "auth-points" },
        point("Transparent, centre-by-centre pricing"),
        point("Preparation guidance for every test"),
        point("Reschedule or cancel online"),
      ),
      h(
        "div",
        { class: "auth-art", "aria-hidden": "true" },
        h("div", { class: "art-circle c1" }),
        h("div", { class: "art-circle c2" }),
        h("div", { class: "art-sun" }),
        h("div", { class: "art-card" }, h("small", {}, "A little peace of mind"), h("strong", {}, "Starts with knowing."), icon("heart")),
      ),
    ),
    panel,
  );
  requestAnimationFrame(paint);
  return view;
}

function firstName() {
  return state.user.name.split(/\s+/)[0];
}

// =====================================================================================
// Patient: overview
// =====================================================================================

function greeting() {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

function viewHome(main) {
  const reminder = h("div");
  const stats = h("div", {}, skeletons("stat", 4));
  const upcoming = h("div", {}, skeletons("row", 2));

  main.append(
    pageHeader({
      kicker: "Your health, at a glance",
      title: `${greeting()}, ${firstName()}`,
      subtitle: "A little care today goes a long way tomorrow.",
      actions: [h("a", { class: "btn btn-primary", href: "#/tests" }, icon("plus"), "Book a test")],
    }),
    reminder,
    h(
      "section",
      { class: "hero-banner" },
      h(
        "div",
        { class: "hero-copy" },
        eyebrow("Transparent prices"),
        h("h2", {}, "Compare centres. Book in minutes."),
        h("p", {}, "The same test can cost very different amounts at different centres. See every price side by side, then pick the time that suits you."),
        h("a", { class: "btn btn-light", href: "#/tests" }, "Explore tests", icon("arrowRight")),
      ),
      h(
        "div",
        { class: "hero-art", "aria-hidden": "true" },
        h("div", { class: "hero-orb" }, icon("leaf")),
        h("span", { class: "hero-spark s1" }, icon("sparkle")),
        h("span", { class: "hero-spark s2" }, icon("sparkle")),
      ),
    ),
    sectionHeader("Your care, made easy", "Everything in one place"),
    stats,
    sectionHeader("Upcoming & recent", "Your appointments", h("a", { class: "link-button", href: "#/bookings" }, "See all", icon("arrowRight"))),
    upcoming,
  );
  loadHome({ reminder, stats, upcoming });
}

async function loadHome({ reminder, stats, upcoming }) {
  let bookings;
  let testCount = 0;
  try {
    const [bookingPage, tests] = await Promise.all([api("/bookings/", { query: { page_size: 100 } }), api("/tests/", { query: { page_size: 1 } })]);
    bookings = bookingPage.items;
    testCount = tests.total;
  } catch (error) {
    upcoming.replaceChildren(errorState(error, () => loadHome({ reminder, stats, upcoming })));
    stats.replaceChildren();
    return;
  }

  const now = Date.now();
  const future = bookings
    .filter((b) => ["PENDING", "CONFIRMED"].includes(b.status) && new Date(b.appointment_datetime).getTime() > now)
    .sort((a, b) => new Date(a.appointment_datetime) - new Date(b.appointment_datetime));
  const unpaid = future.filter((b) => b.status === "PENDING" && !b.payment_in_progress && (!b.hold_expires_at || new Date(b.hold_expires_at) > now));
  const completed = bookings.filter((b) => b.status === "COMPLETED").length;

  stats.replaceChildren(
    h(
      "div",
      { class: "stat-grid" },
      statCard({ iconName: "calendar", value: String(future.filter((b) => b.status === "CONFIRMED").length), label: "Confirmed visits", onClick: () => navigate("bookings") }),
      statCard({ iconName: "card", tone: "peach", value: String(unpaid.length), label: "Awaiting payment", onClick: () => navigate("bookings") }),
      statCard({ iconName: "check", tone: "blue", value: String(completed), label: "Tests completed" }),
      statCard({ iconName: "flask", value: String(testCount), label: "Tests to explore", onClick: () => navigate("tests") }),
    ),
  );

  const refresh = () => loadHome({ reminder, stats, upcoming });
  const shown = (future.length ? future : bookings).slice(0, 3);
  upcoming.replaceChildren(
    shown.length
      ? h("div", { class: "list" }, shown.map((booking) => bookingRow(booking, refresh)))
      : emptyState("calendar", "No appointments yet", "When you book a test, it will show up here.", h("a", { class: "btn btn-primary btn-sm", href: "#/tests" }, "Find a test")),
  );

  reminder.replaceChildren();
  if (unpaid.length) {
    const next = unpaid[0];
    reminder.append(
      reminderBanner(
        "card",
        `${next.test_name} is reserved but not paid`,
        next.hold_expires_at ? `Pay by ${formatDateTime(next.hold_expires_at)} to keep your slot on ${formatDateTime(next.appointment_datetime)}.` : `Pay to confirm your slot on ${formatDateTime(next.appointment_datetime)}.`,
        h("a", { class: "btn btn-primary btn-sm", href: "#/bookings" }, "Pay now"),
      ),
    );
    return;
  }
  const soon = future.find((b) => b.status === "CONFIRMED" && new Date(b.appointment_datetime).getTime() - now < 48 * 3600 * 1000);
  if (soon) {
    const test = await cached(`/tests/${soon.test_id}`).catch(() => null);
    const prep = test?.preparation_instructions || (test?.fasting_hours ? `Remember to fast for ${test.fasting_hours} hours beforehand.` : "");
    reminder.append(reminderBanner("clock", `${soon.test_name} at ${soon.centre_name}, ${formatDateTime(soon.appointment_datetime)}`, prep || "See you soon. Bring a photo ID.", null));
  }
}

function reminderBanner(iconName, title, text, action) {
  return h("div", { class: "reminder", role: "status" }, h("span", { class: "reminder-icon" }, icon(iconName)), h("div", {}, h("strong", {}, title), h("p", {}, text)), action);
}

// =====================================================================================
// Patient: tests & centres
// =====================================================================================

function viewTests(main) {
  const results = h("div");
  let search = "";
  const list = paginatedList(results, {
    fetchPage: (page) => api("/tests/", { query: { page, page_size: 12, search } }),
    loading: () => skeletons("card", 6),
    empty: () => emptyState("flask", search ? "No matching tests" : "No tests yet", search ? "Try a different search term." : "The catalogue is empty for now."),
    renderItems: (items) => h("div", { class: "grid" }, items.map(testCard)),
  });

  main.append(
    pageHeader({ kicker: "A moment for yourself", title: "Find the right test", subtitle: "Compare prices across centres, check how to prepare, and book a slot in a few clicks." }),
    h(
      "div",
      { class: "toolbar" },
      searchInput("Search tests, e.g. blood, thyroid…", (value) => {
        search = value;
        list.reset();
      }),
    ),
    results,
  );
  list.reload();
}

function testCard(test) {
  return h(
    "article",
    { class: "card item-card" },
    h("div", { class: "item-card-head" }, h("span", { class: "item-icon" }, icon("flask")), h("div", {}, h("h3", {}, test.name), h("p", { class: "desc" }, test.description || "No description provided."))),
    testChips(test),
    h(
      "div",
      { class: "item-card-foot" },
      h("div", { class: "price-label" }, h("span", {}, "From list price"), h("strong", {}, formatMoney(test.base_price))),
      h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => openTestOffers(test) }, "Compare centres"),
    ),
  );
}

async function openTestOffers(test) {
  const modal = openModal({ kicker: "Compare centres", title: test.name, subtitle: "Every centre offering this test, cheapest first.", body: skeletons("row", 3), wide: true });
  try {
    const data = await api(`/tests/${test.id}/centres`, { query: { page_size: 100 } });
    if (!data.items.length) {
      modal.setBody(emptyState("building", "Not available yet", "No centre offers this test at the moment."));
      return;
    }
    const centres = await Promise.all(data.items.map((offer) => cached(`/centres/${offer.centre_id}`)));
    const cheapest = Number(data.items[0].price);
    modal.setBody(
      h(
        "div",
        { class: "stack" },
        prepCallout(test),
        h(
          "div",
          { class: "offer-list" },
          data.items.map((offer, index) =>
            offerRow({
              title: offer.centre_name,
              subtitle: h("span", { class: "meta-line" }, icon("pin"), centres[index].location),
              price: offer.price,
              badge: index === 0 && data.items.length > 1 ? "Best price" : Number(offer.price) > cheapest ? `+${formatMoney(Number(offer.price) - cheapest)}` : null,
              onBook: () => {
                modal.close();
                openBookingModal({ test, centre: centres[index], price: offer.price });
              },
            }),
          ),
        ),
      ),
    );
  } catch (error) {
    modal.setBody(errorState(error));
  }
}

function offerRow({ title, subtitle, price, badge, onBook }) {
  return h(
    "div",
    { class: "offer" },
    h("div", { class: "offer-main" }, h("strong", {}, title, badge ? h("span", { class: "tag" }, badge) : null), subtitle),
    h("span", { class: "offer-price" }, formatMoney(price)),
    h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: onBook }, "Book"),
  );
}

function viewCentres(main) {
  const results = h("div");
  const filters = { search: "", location: "" };
  const list = paginatedList(results, {
    fetchPage: (page) => api("/centres/", { query: { page, page_size: 12, ...filters } }),
    loading: () => skeletons("card", 6),
    empty: () => emptyState("building", "No centres found", "Try adjusting the filters."),
    renderItems: (items) => h("div", { class: "grid" }, items.map(centreCard)),
  });

  main.append(
    pageHeader({ kicker: "Near you", title: "Diagnostic centres", subtitle: "Browse centres and see every test they offer, with their prices." }),
    h(
      "div",
      { class: "toolbar" },
      searchInput("Search by centre name", (value) => {
        filters.search = value;
        list.reset();
      }),
      searchInput("Filter by location, e.g. Delhi", (value) => {
        filters.location = value;
        list.reset();
      }),
    ),
    results,
  );
  list.reload();
}

function centreCard(centre) {
  return h(
    "article",
    { class: "card item-card" },
    h("div", { class: "item-card-head" }, h("span", { class: "item-icon" }, icon("building")), h("div", {}, h("h3", {}, centre.name), h("p", { class: "meta-line" }, icon("pin"), centre.location))),
    h(
      "div",
      { class: "item-card-foot" },
      h("span", { class: "small subtle" }, `Listed ${formatDate(centre.created_at)}`),
      h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => openCentreOffers(centre) }, "Tests & prices"),
    ),
  );
}

async function openCentreOffers(centre) {
  const modal = openModal({ kicker: "Tests & prices", title: centre.name, subtitle: centre.location, body: skeletons("row", 3), wide: true });
  try {
    const data = await api(`/centres/${centre.id}/tests`, { query: { page_size: 100 } });
    if (!data.items.length) {
      modal.setBody(emptyState("flask", "No tests listed", "This centre doesn't offer any tests yet."));
      return;
    }
    const tests = await Promise.all(data.items.map((offer) => cached(`/tests/${offer.test_id}`)));
    modal.setBody(
      h(
        "div",
        { class: "offer-list" },
        data.items.map((offer, index) =>
          offerRow({
            title: offer.test_name,
            subtitle: testChips(tests[index]) ?? h("span", { class: "small muted" }, tests[index].description || ""),
            price: offer.price,
            onBook: () => {
              modal.close();
              openBookingModal({ test: tests[index], centre, price: offer.price });
            },
          }),
        ),
      ),
    );
  } catch (error) {
    modal.setBody(errorState(error));
  }
}

// =====================================================================================
// Patient: bookings
// =====================================================================================

function viewBookings(main) {
  main.append(
    pageHeader({
      kicker: "Your care plan",
      title: "My bookings",
      subtitle: "Pay, reschedule or cancel upcoming appointments, and see how to prepare for each one.",
      actions: [h("a", { class: "btn btn-primary", href: "#/tests" }, icon("plus"), "New booking")],
    }),
  );
  bookingsSection(main);
}

boot().catch((error) => {
  root.replaceChildren(errorState(error instanceof ApiError ? error : new Error("Failed to start the app.")));
});
