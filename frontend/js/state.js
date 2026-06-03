// Shared, mutable browse state (one object, imported by reference).
export const state = {
  tab: "live",        // live | movie | series | imported
  group: null,
  q: "",
  page: 1,
  pageSize: 100,
  total: 0,
};
