// Memory section barrel (Phase 11 dashboard).
// Router/resource registration imports the four memory pages from here.

export { MemoryList, default as MemoryListDefault } from "./list";
export { MemoryShow, default as MemoryShowDefault } from "./show";
export { MemorySearch, default as MemorySearchDefault } from "./search";
export { MemoryStats, default as MemoryStatsDefault } from "./stats";
export { MemoryGraph, default as MemoryGraphDefault } from "./graph";
export * from "./types";
