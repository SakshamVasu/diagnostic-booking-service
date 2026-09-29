// Shared app state and navigation, used by every view module.

import { errorState, pager } from "./ui.js";

export const state = { user: null };

export const isAdmin = () => state.user?.role === "ADMIN";

export function navigate(route) {
  if (location.hash === `#/${route}`) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = `#/${route}`;
}

/**
 * Renders a paginated collection into `container`.
 * `fetchPage(page)` returns the API page; `renderItems(items)` returns a node.
 */
export function paginatedList(container, { fetchPage, renderItems, loading, empty, onPage }) {
  let page = 1;
  const load = async () => {
    container.replaceChildren(loading());
    try {
      const data = await fetchPage(page);
      onPage?.(data);
      if (!data.items.length) {
        container.replaceChildren(empty());
        return;
      }
      const nav = pager({
        ...data,
        onChange: (next) => {
          page = next;
          load();
          window.scrollTo({ top: 0, behavior: "smooth" });
        },
      });
      container.replaceChildren(...[await renderItems(data.items), nav].filter(Boolean));
    } catch (error) {
      container.replaceChildren(errorState(error, load));
    }
  };
  return {
    reload: load,
    reset: () => {
      page = 1;
      return load();
    },
  };
}
