// Thin, typed client for the PulseSearch API. Keeps all network concerns in one
// place so components stay declarative.

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

export type SearchMode = "hybrid" | "bm25" | "vector";

export interface PageDocument {
  id: string;
  wiki: string;
  title: string;
  title_url?: string | null;
  last_comment?: string | null;
  last_user?: string | null;
  event_type?: string | null;
  edit_count?: number;
  event_time?: string | null;
}

export interface SearchHit {
  id: string;
  score: number;
  document: PageDocument;
  bm25_rank?: number | null;
  knn_rank?: number | null;
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  count: number;
  took_ms: number;
  hits: SearchHit[];
}

export interface Citation {
  id: string;
  title: string;
  title_url?: string | null;
  wiki: string;
  event_time?: string | null;
}

export interface RAGAnswer {
  answer: string;
  citations: Citation[];
  grounded: boolean;
  freshest_source?: string | null;
}

export interface Readiness {
  status: string;
  elasticsearch: boolean;
  documents: number;
  llm: boolean;
}

export interface LiveEvent {
  op: string;
  id: string;
  wiki?: string;
  title?: string;
  title_url?: string;
  last_user?: string;
  event_type?: string;
  edit_count?: number;
  ts_ms?: number;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export async function search(
  query: string,
  mode: SearchMode,
  size = 10
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, mode, size: String(size) });
  return getJSON<SearchResponse>(`/search?${params.toString()}`);
}

export async function ask(question: string): Promise<RAGAnswer> {
  const res = await fetch(`${API_URL}/rag`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(`POST /rag -> ${res.status}`);
  return res.json() as Promise<RAGAnswer>;
}

export async function readiness(): Promise<Readiness> {
  return getJSON<Readiness>("/health/ready");
}
