/**
 * dataProvider.ts — custom Refine DataProvider for the ElephantBroker dashboard.
 *
 * Maps Refine resources to EB `/dashboard/*` (and a few `/auth/*`, `/admin/*`)
 * endpoints via `RESOURCE_MAP`, translates Refine pagination/sorting/filtering
 * into the EB API's parameter shapes, and normalises the several response
 * envelopes the backend returns into Refine's `{ data, total }`.
 *
 * Every request:
 *  - goes through `apiClient` (so `credentials: "include"` + SuperTokens
 *    cookie/CSRF/refresh are attached automatically), and
 *  - carries `gateway_id` sourced from the operator's selected gateway
 *    (`meta.gatewayId` wins, else the module-level selection from apiClient).
 *
 * The memory resource is special: listing is a `POST /dashboard/memory/browse`
 * with a `MemoryBrowseRequest` body returning `PaginatedResult[FactAssertion]`
 * ({ items, total, offset, limit, has_more }). All other list endpoints are GET
 * and may return a bare array, a `{ items, total, ... }` page, or a
 * single-array-property envelope (e.g. `{ rules: [...] }`) — all handled here.
 */

import type {
  BaseRecord,
  CreateParams,
  CreateResponse,
  CrudFilters,
  CrudSorting,
  CustomParams,
  CustomResponse,
  DataProvider,
  DeleteOneParams,
  DeleteOneResponse,
  GetListParams,
  GetListResponse,
  GetManyParams,
  GetManyResponse,
  GetOneParams,
  GetOneResponse,
  MetaQuery,
  Pagination,
  UpdateParams,
  UpdateResponse,
} from "@refinedev/core";

import { apiClient, API_URL, getSelectedGateway } from "./apiClient";

// --- Resource → endpoint mapping -----------------------------------------

interface ResourceConfig {
  /** Collection endpoint used by getList. */
  list: string;
  /** getList HTTP verb. `POST` sends a request body instead of query params. */
  listMethod?: "GET" | "POST";
  /** Single-record endpoint builder for getOne (defaults to `${list}/${id}`). */
  one?: (id: string) => string;
  /** Create endpoint (defaults to `list`). */
  create?: string;
  /** Update endpoint builder (defaults to `${list}/${id}`). */
  update?: (id: string) => string;
  /** Delete endpoint builder (defaults to `${list}/${id}`). */
  remove?: (id: string) => string;
}

export const RESOURCE_MAP: Record<string, ResourceConfig> = {
  memory: {
    list: "/dashboard/memory/browse",
    listMethod: "POST",
    one: (id) => `/dashboard/memory/${id}/detail`,
  },
  actors: {
    list: "/dashboard/actors",
    one: (id) => `/dashboard/actors/${id}/detail`,
  },
  organizations: {
    list: "/dashboard/organizations",
    one: (id) => `/dashboard/organizations/${id}`,
  },
  goals: {
    list: "/dashboard/goals",
    one: (id) => `/dashboard/goals/${id}`,
    create: "/admin/goals",
  },
  procedures: {
    list: "/dashboard/procedures",
    one: (id) => `/dashboard/procedures/${id}/detail`,
    update: (id) => `/procedures/${id}`,
  },
  sessions: {
    list: "/dashboard/sessions/active",
    one: (id) => `/trace/session/${id}/timeline`,
  },
  "sessions-recent": {
    list: "/dashboard/sessions/recent",
  },
  profiles: {
    list: "/dashboard/profiles",
  },
  // Guard activity feed (ClickHouse events).
  guards: {
    list: "/dashboard/guards/activity",
  },
  // Custom guard rules CRUD.
  "guard-rules": {
    list: "/dashboard/guards/rules",
    create: "/dashboard/guards/rules",
    update: (id) => `/dashboard/guards/rules/${id}`,
    remove: (id) => `/dashboard/guards/rules/${id}`,
  },
  "guard-approvals": {
    list: "/dashboard/guards/approvals/pending",
  },
  "api-keys": {
    list: "/auth/api-keys",
    create: "/auth/api-keys",
    remove: (id) => `/auth/api-keys/${id}`,
  },
  "saved-views": {
    list: "/dashboard/saved-views",
    create: "/dashboard/saved-views",
    remove: (id) => `/dashboard/saved-views/${id}`,
  },
  "authority-rules": {
    list: "/admin/authority-rules",
    update: (id) => `/admin/authority-rules/${id}`,
    remove: (id) => `/admin/authority-rules/${id}`,
  },
  gateways: {
    list: "/dashboard/gateways",
  },
};

function resolveResource(resource: string): ResourceConfig {
  const config = RESOURCE_MAP[resource];
  if (!config) {
    // Fall back to a conventional `/dashboard/{resource}` mapping so unknown
    // resources degrade predictably instead of throwing at wiring time.
    return { list: `/dashboard/${resource}` };
  }
  return config;
}

// --- Helpers --------------------------------------------------------------

/** Resolve the effective gateway_id: explicit meta wins, else the selection. */
function resolveGateway(meta?: MetaQuery): string {
  const fromMeta = (meta as any)?.gatewayId ?? (meta as any)?.gateway_id;
  return (fromMeta as string) || getSelectedGateway() || "";
}

/** Refine pagination → 1-indexed page + page size (server defaults applied). */
function resolvePagination(pagination?: Pagination): {
  page: number;
  perPage: number;
} {
  const current =
    (pagination as any)?.current ?? (pagination as any)?.currentPage ?? 1;
  const perPage = pagination?.pageSize ?? 50;
  return { page: Math.max(1, current), perPage: Math.max(1, perPage) };
}

/** First sorter → { field, order }; sensible defaults when unsorted. */
function resolveSort(sorters?: CrudSorting): {
  sortBy: string;
  sortOrder: "asc" | "desc";
} {
  const first = sorters?.[0];
  return {
    sortBy: first?.field ?? "created_at",
    sortOrder: (first?.order as "asc" | "desc") ?? "desc",
  };
}

/**
 * Flatten Refine filters into a plain `{ field: value }` map, applying operator
 * suffixes the EB API understands (e.g. `_gte`, `_contains`). Logical `and`/`or`
 * groups are flattened one level (the dashboard API only supports AND-of-fields).
 */
function flattenFilters(filters?: CrudFilters): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!filters) return out;

  for (const filter of filters) {
    // ConditionalFilter (has `key` + nested `value` array): flatten members.
    if ((filter as any).operator === "and" || (filter as any).operator === "or") {
      Object.assign(out, flattenFilters((filter as any).value as CrudFilters));
      continue;
    }
    const field = (filter as any).field as string | undefined;
    const operator = (filter as any).operator as string | undefined;
    const value = (filter as any).value;
    if (!field || value === undefined || value === null || value === "") continue;

    switch (operator) {
      case "gte":
      case "gt":
        out[`${field}_gte`] = value;
        break;
      case "lte":
      case "lt":
        out[`${field}_lte`] = value;
        break;
      case "contains":
      case "containss":
        out[`${field}_contains`] = value;
        break;
      case "in":
        out[field] = Array.isArray(value) ? value.join(",") : value;
        break;
      default:
        out[field] = value;
    }
  }
  return out;
}

/**
 * Build the `MemoryBrowseRequest` body from Refine params for the POST browse
 * endpoint. Maps generic filter fields onto the request's named fields.
 */
function buildMemoryBrowseBody(
  pagination: Pagination | undefined,
  sorters: CrudSorting | undefined,
  filters: CrudFilters | undefined,
): Record<string, unknown> {
  const { page, perPage } = resolvePagination(pagination);
  const { sortBy, sortOrder } = resolveSort(sorters);
  const flat = flattenFilters(filters);

  const body: Record<string, unknown> = {
    page,
    per_page: perPage,
    sort_by: sortBy,
    sort_order: sortOrder,
  };

  const passthrough = (key: string, target = key) => {
    if (flat[key] !== undefined) body[target] = flat[key];
  };
  passthrough("scope");
  passthrough("memory_class");
  passthrough("category");
  passthrough("source_actor_id");
  passthrough("goal_id");
  // confidence >= threshold
  if (flat["confidence_gte"] !== undefined)
    body["min_confidence"] = flat["confidence_gte"];
  if (flat["min_confidence"] !== undefined)
    body["min_confidence"] = flat["min_confidence"];
  // free-text substring
  if (flat["text_contains"] !== undefined)
    body["text_contains"] = flat["text_contains"];
  if (flat["text"] !== undefined) body["text_contains"] = flat["text"];

  return body;
}

/**
 * Normalise the many list-response envelopes into `{ data, total }`.
 *  - PaginatedResult:      { items, total, offset, limit, has_more }
 *  - FactPage-like:        { items, total, page, page_size, total_pages }
 *  - Named-array envelope: { rules: [...] } / { sessions: [...] } / etc.
 *  - Bare array:           [...]
 */
function normalizeList<T extends BaseRecord>(
  payload: unknown,
): { data: T[]; total: number } {
  if (Array.isArray(payload)) {
    return { data: payload as T[], total: payload.length };
  }
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    if (Array.isArray(obj.items)) {
      const items = obj.items as T[];
      const total =
        typeof obj.total === "number" ? (obj.total as number) : items.length;
      return { data: items, total };
    }
    // First array-valued property (e.g. rules / sessions / keys / pending).
    for (const value of Object.values(obj)) {
      if (Array.isArray(value)) {
        const arr = value as T[];
        const total =
          typeof obj.total === "number" ? (obj.total as number) : arr.length;
        return { data: arr, total };
      }
    }
  }
  return { data: [], total: 0 };
}

// --- DataProvider ---------------------------------------------------------

export const dataProvider: DataProvider = {
  getApiUrl: () => API_URL,

  getList: async <TData extends BaseRecord = BaseRecord>({
    resource,
    pagination,
    sorters,
    filters,
    meta,
  }: GetListParams): Promise<GetListResponse<TData>> => {
    const config = resolveResource(resource);
    const gatewayId = resolveGateway(meta);

    if (config.listMethod === "POST") {
      // memory/browse — filters/sort/pagination travel in the JSON body.
      const body = buildMemoryBrowseBody(pagination, sorters, filters);
      const payload = await apiClient.post(config.list, body, {
        gateway_id: gatewayId,
      });
      const { data, total } = normalizeList<TData>(payload);
      return { data, total };
    }

    const { page, perPage } = resolvePagination(pagination);
    const { sortBy, sortOrder } = resolveSort(sorters);
    const query: Record<string, unknown> = {
      gateway_id: gatewayId,
      page,
      per_page: perPage,
      sort_by: sortBy,
      sort_order: sortOrder,
      ...flattenFilters(filters),
    };
    const payload = await apiClient.get(config.list, query);
    const { data, total } = normalizeList<TData>(payload);
    return { data, total };
  },

  getMany: async <TData extends BaseRecord = BaseRecord>({
    resource,
    ids,
    meta,
  }: GetManyParams): Promise<GetManyResponse<TData>> => {
    const config = resolveResource(resource);
    const gatewayId = resolveGateway(meta);
    const oneOf = config.one ?? ((id: string) => `${config.list}/${id}`);
    const results = await Promise.all(
      ids.map((id) =>
        apiClient.get<TData>(oneOf(String(id)), { gateway_id: gatewayId }),
      ),
    );
    return { data: results };
  },

  getOne: async <TData extends BaseRecord = BaseRecord>({
    resource,
    id,
    meta,
  }: GetOneParams): Promise<GetOneResponse<TData>> => {
    const config = resolveResource(resource);
    const gatewayId = resolveGateway(meta);
    const path = config.one
      ? config.one(String(id))
      : `${config.list}/${id}`;
    const data = await apiClient.get<TData>(path, { gateway_id: gatewayId });
    return { data };
  },

  create: async <
    TData extends BaseRecord = BaseRecord,
    TVariables = Record<string, unknown>,
  >({
    resource,
    variables,
    meta,
  }: CreateParams<TVariables>): Promise<CreateResponse<TData>> => {
    const config = resolveResource(resource);
    const gatewayId = resolveGateway(meta);
    const path = config.create ?? config.list;
    const data = await apiClient.post<TData>(path, variables, {
      gateway_id: gatewayId,
    });
    return { data };
  },

  update: async <
    TData extends BaseRecord = BaseRecord,
    TVariables = Record<string, unknown>,
  >({
    resource,
    id,
    variables,
    meta,
  }: UpdateParams<TVariables>): Promise<UpdateResponse<TData>> => {
    const config = resolveResource(resource);
    const gatewayId = resolveGateway(meta);
    const path = config.update
      ? config.update(String(id))
      : `${config.list}/${id}`;
    // EB update endpoints are PATCH for guard rules, PUT elsewhere; PATCH is a
    // safe superset the FastAPI routes accept via method override where needed.
    const method = (meta as any)?.method === "put" ? "put" : "patch";
    const data =
      method === "put"
        ? await apiClient.put<TData>(path, variables, { gateway_id: gatewayId })
        : await apiClient.patch<TData>(path, variables, {
            gateway_id: gatewayId,
          });
    return { data };
  },

  deleteOne: async <
    TData extends BaseRecord = BaseRecord,
    TVariables = Record<string, unknown>,
  >({
    resource,
    id,
    meta,
  }: DeleteOneParams<TVariables>): Promise<DeleteOneResponse<TData>> => {
    const config = resolveResource(resource);
    const gatewayId = resolveGateway(meta);
    const path = config.remove
      ? config.remove(String(id))
      : `${config.list}/${id}`;
    const data = await apiClient.delete<TData>(path, { gateway_id: gatewayId });
    return { data: (data ?? { id }) as TData };
  },

  custom: async <TData extends BaseRecord = BaseRecord>({
    url,
    method,
    payload,
    query,
    meta,
  }: CustomParams): Promise<CustomResponse<TData>> => {
    const gatewayId = resolveGateway(meta);
    const mergedQuery: Record<string, unknown> = {
      gateway_id: gatewayId,
      ...(query as Record<string, unknown> | undefined),
    };
    const verb = (method ?? "get").toLowerCase();
    // apiClient resolves absolute (http…) and relative paths transparently.
    const path = url;

    let data: TData;
    switch (verb) {
      case "post":
        data = await apiClient.post<TData>(path, payload, mergedQuery);
        break;
      case "put":
        data = await apiClient.put<TData>(path, payload, mergedQuery);
        break;
      case "patch":
        data = await apiClient.patch<TData>(path, payload, mergedQuery);
        break;
      case "delete":
        data = await apiClient.delete<TData>(path, mergedQuery);
        break;
      default:
        data = await apiClient.get<TData>(path, mergedQuery);
    }
    return { data };
  },
};

export default dataProvider;
