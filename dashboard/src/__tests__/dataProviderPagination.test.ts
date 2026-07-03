/**
 * dataProviderPagination.test.ts — unit tests for the NEW page-size-from-
 * preference behaviour added on EB-FE.
 *
 * `resolvePagination()` (exercised here through `dataProvider.getList`) now
 * honours an explicitly-passed `pagination.pageSize` and, when none is passed,
 * falls back to the operator's saved "items per page" preference read from
 * `localStorage["eb:pref:items_per_page"]` (settings-3), defaulting to 50 when
 * the preference is absent, non-positive, unparseable, or unreadable.
 *
 * The EB `apiClient` and `localStorage` are fully mocked — no network, no real
 * storage. We assert on the `per_page` the provider sends: the GET query for a
 * normal resource, and the POST `/browse` body for the memory resource.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../providers/apiClient", () => ({
  apiClient: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  API_URL: "http://test.local",
  getSelectedGateway: () => "gw-default",
}));

import { dataProvider } from "../providers/dataProvider";

const ITEMS_PER_PAGE_KEY = "eb:pref:items_per_page";
const FALLBACK_PAGE_SIZE = 50;

// --- localStorage mock ----------------------------------------------------
let store: Record<string, string> = {};
const getItem = vi.fn((key: string) => (key in store ? store[key] : null));
const localStorageMock = {
  getItem,
  setItem: vi.fn((k: string, v: string) => {
    store[k] = String(v);
  }),
  removeItem: vi.fn((k: string) => {
    delete store[k];
  }),
  clear: vi.fn(() => {
    store = {};
  }),
  key: vi.fn(),
  length: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  store = {};
  Object.defineProperty(window, "localStorage", {
    value: localStorageMock,
    writable: true,
    configurable: true,
  });
  // Default stubbed responses so getList resolves without a real backend.
  apiGet.mockResolvedValue([]);
  apiPost.mockResolvedValue({ items: [], total: 0 });
});

/** Run getList for a plain GET resource and return the `per_page` it sent. */
async function perPageForGet(pagination: unknown): Promise<number> {
  await dataProvider.getList({
    resource: "actors",
    pagination,
    sorters: [],
    filters: [],
    meta: {},
  } as any);
  const [, query] = apiGet.mock.calls[apiGet.mock.calls.length - 1];
  return (query as Record<string, number>).per_page;
}

/** Run getList for the memory resource and return the browse body `per_page`. */
async function perPageForMemoryBrowse(pagination: unknown): Promise<number> {
  await dataProvider.getList({
    resource: "memory",
    pagination,
    sorters: [],
    filters: [],
    meta: {},
  } as any);
  const [, body] = apiPost.mock.calls[apiPost.mock.calls.length - 1];
  return (body as Record<string, number>).per_page;
}

describe("resolvePagination: page size from saved preference (no pageSize)", () => {
  it("defaults per_page from eb:pref:items_per_page when no pageSize is passed", async () => {
    store[ITEMS_PER_PAGE_KEY] = "25";
    const perPage = await perPageForGet({ current: 1 });
    expect(perPage).toBe(25);
    expect(getItem).toHaveBeenCalledWith(ITEMS_PER_PAGE_KEY);
  });

  it("applies the saved preference for the memory POST /browse body too", async () => {
    store[ITEMS_PER_PAGE_KEY] = "30";
    const perPage = await perPageForMemoryBrowse({ current: 1 });
    expect(perPage).toBe(30);
  });

  it("reads the preference when pagination itself is undefined", async () => {
    store[ITEMS_PER_PAGE_KEY] = "15";
    const perPage = await perPageForGet(undefined);
    expect(perPage).toBe(15);
  });
});

describe("resolvePagination: explicit pageSize wins over the preference", () => {
  it("uses the passed pageSize and ignores the saved preference", async () => {
    store[ITEMS_PER_PAGE_KEY] = "25";
    const perPage = await perPageForGet({ current: 1, pageSize: 10 });
    expect(perPage).toBe(10);
  });

  it("does not even read localStorage when a pageSize is provided", async () => {
    store[ITEMS_PER_PAGE_KEY] = "25";
    await perPageForGet({ current: 2, pageSize: 40 });
    expect(getItem).not.toHaveBeenCalledWith(ITEMS_PER_PAGE_KEY);
  });
});

describe("resolvePagination: fallback to 50 when preference is unusable", () => {
  it("falls back to 50 when no preference is stored", async () => {
    const perPage = await perPageForGet({ current: 1 });
    expect(perPage).toBe(FALLBACK_PAGE_SIZE);
  });

  it("falls back to 50 when the stored value is not a positive integer", async () => {
    store[ITEMS_PER_PAGE_KEY] = "0";
    expect(await perPageForGet({ current: 1 })).toBe(FALLBACK_PAGE_SIZE);

    store[ITEMS_PER_PAGE_KEY] = "-5";
    expect(await perPageForGet({ current: 1 })).toBe(FALLBACK_PAGE_SIZE);
  });

  it("falls back to 50 when the stored value is non-numeric", async () => {
    store[ITEMS_PER_PAGE_KEY] = "not-a-number";
    const perPage = await perPageForGet({ current: 1 });
    expect(perPage).toBe(FALLBACK_PAGE_SIZE);
  });

  it("falls back to 50 when localStorage.getItem throws", async () => {
    getItem.mockImplementationOnce(() => {
      throw new Error("storage access denied");
    });
    const perPage = await perPageForGet({ current: 1 });
    expect(perPage).toBe(FALLBACK_PAGE_SIZE);
  });
});
