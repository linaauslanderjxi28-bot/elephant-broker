/**
 * dataProvider.test.ts — unit tests for the custom Refine DataProvider. The EB
 * `apiClient` is mocked so the tests verify resource→endpoint mapping, the
 * memory `POST /browse` special case, gateway scoping (meta override vs. the
 * module-level selection), and the response-envelope normalisation — all without
 * any real network calls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPut = vi.fn();
const apiPatch = vi.fn();
const apiDelete = vi.fn();

vi.mock("../providers/apiClient", () => ({
  apiClient: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    put: (...a: unknown[]) => apiPut(...a),
    patch: (...a: unknown[]) => apiPatch(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
  API_URL: "http://test.local",
  getSelectedGateway: () => "gw-default",
}));

import { dataProvider, RESOURCE_MAP } from "../providers/dataProvider";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RESOURCE_MAP", () => {
  it("routes memory list through POST /browse", () => {
    expect(RESOURCE_MAP.memory.list).toBe("/dashboard/memory/browse");
    expect(RESOURCE_MAP.memory.listMethod).toBe("POST");
  });

  it("maps known resources to /dashboard endpoints", () => {
    expect(RESOURCE_MAP.actors.list).toBe("/dashboard/actors");
    expect(RESOURCE_MAP.goals.create).toBe("/admin/goals");
  });
});

describe("dataProvider.getApiUrl", () => {
  it("returns the configured API_URL", () => {
    expect(dataProvider.getApiUrl()).toBe("http://test.local");
  });
});

describe("dataProvider.getList", () => {
  it("issues a GET with gateway + pagination for a normal resource", async () => {
    apiGet.mockResolvedValue({ items: [{ id: "a" }, { id: "b" }], total: 2 });
    const res = await dataProvider.getList({
      resource: "actors",
      pagination: { current: 2, pageSize: 25 },
      sorters: [{ field: "name", order: "asc" }],
      filters: [],
      meta: {},
    } as any);

    expect(res.total).toBe(2);
    expect(res.data).toHaveLength(2);
    const [path, query] = apiGet.mock.calls[0];
    expect(path).toBe("/dashboard/actors");
    expect(query).toMatchObject({
      gateway_id: "gw-default",
      page: 2,
      per_page: 25,
      sort_by: "name",
      sort_order: "asc",
    });
  });

  it("issues a POST browse body for the memory resource", async () => {
    apiPost.mockResolvedValue({
      items: [{ id: "f1" }],
      total: 1,
      has_more: false,
    });
    const res = await dataProvider.getList({
      resource: "memory",
      pagination: { current: 1, pageSize: 50 },
      sorters: [{ field: "created_at", order: "desc" }],
      filters: [
        { field: "scope", operator: "eq", value: "team" },
        { field: "confidence", operator: "gte", value: 0.8 },
      ],
      meta: {},
    } as any);

    expect(res.total).toBe(1);
    const [path, body, query] = apiPost.mock.calls[0];
    expect(path).toBe("/dashboard/memory/browse");
    expect(body).toMatchObject({
      page: 1,
      per_page: 50,
      sort_by: "created_at",
      sort_order: "desc",
      scope: "team",
      min_confidence: 0.8,
    });
    expect(query).toEqual({ gateway_id: "gw-default" });
  });

  it("prefers meta.gatewayId over the module selection", async () => {
    apiGet.mockResolvedValue([]);
    await dataProvider.getList({
      resource: "profiles",
      pagination: { current: 1, pageSize: 10 },
      sorters: [],
      filters: [],
      meta: { gatewayId: "gw-override" },
    } as any);
    const [, query] = apiGet.mock.calls[0];
    expect(query.gateway_id).toBe("gw-override");
  });

  it("normalises a named-array envelope (e.g. guard rules)", async () => {
    apiGet.mockResolvedValue({ rules: [{ id: "r1" }, { id: "r2" }] });
    const res = await dataProvider.getList({
      resource: "guard-rules",
      pagination: { current: 1, pageSize: 10 },
      sorters: [],
      filters: [],
      meta: {},
    } as any);
    expect(res.data).toHaveLength(2);
    expect(res.total).toBe(2);
  });

  it("normalises a bare array response", async () => {
    apiGet.mockResolvedValue([{ id: "x" }, { id: "y" }, { id: "z" }]);
    const res = await dataProvider.getList({
      resource: "profiles",
      pagination: { current: 1, pageSize: 10 },
      sorters: [],
      filters: [],
      meta: {},
    } as any);
    expect(res.total).toBe(3);
  });
});

describe("dataProvider.getOne", () => {
  it("uses the resource-specific detail path", async () => {
    apiGet.mockResolvedValue({ id: "act-1", display_name: "Ada" });
    const res = await dataProvider.getOne({
      resource: "actors",
      id: "act-1",
      meta: {},
    } as any);
    expect(res.data).toMatchObject({ id: "act-1" });
    const [path, query] = apiGet.mock.calls[0];
    expect(path).toBe("/dashboard/actors/act-1/detail");
    expect(query).toEqual({ gateway_id: "gw-default" });
  });
});

describe("dataProvider.deleteOne", () => {
  it("hits the remove endpoint and returns a fallback record", async () => {
    apiDelete.mockResolvedValue(undefined);
    const res = await dataProvider.deleteOne({
      resource: "guard-rules",
      id: "r9",
      meta: {},
    } as any);
    const [path] = apiDelete.mock.calls[0];
    expect(path).toBe("/dashboard/guards/rules/r9");
    expect(res.data).toMatchObject({ id: "r9" });
  });
});

describe("dataProvider.update", () => {
  it("defaults to PATCH and honours meta.method=put", async () => {
    apiPatch.mockResolvedValue({ id: "r1" });
    await dataProvider.update({
      resource: "guard-rules",
      id: "r1",
      variables: { enabled: false },
      meta: {},
    } as any);
    expect(apiPatch).toHaveBeenCalledTimes(1);
    expect(apiPut).not.toHaveBeenCalled();

    apiPut.mockResolvedValue({ id: "r2" });
    await dataProvider.update({
      resource: "guard-rules",
      id: "r2",
      variables: { enabled: true },
      meta: { method: "put" },
    } as any);
    expect(apiPut).toHaveBeenCalledTimes(1);
  });
});
