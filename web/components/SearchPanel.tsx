"use client";

import { useState } from "react";
import { search, SearchHit, SearchMode } from "@/lib/api";

const MODES: { key: SearchMode; label: string }[] = [
  { key: "hybrid", label: "Hybrid (RRF)" },
  { key: "bm25", label: "BM25" },
  { key: "vector", label: "Vector" },
];

export default function SearchPanel() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [took, setTook] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const run = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await search(query, mode, 10);
      setHits(res.hits);
      setTook(res.took_ms);
      setSearched(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h2>Hybrid search</h2>
      <div className="controls">
        <input
          type="text"
          placeholder="Search recently changed pages…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <button onClick={run} disabled={loading}>
          {loading ? <span className="spinner" /> : "Search"}
        </button>
      </div>

      <div className="controls">
        <div className="segmented">
          {MODES.map((m) => (
            <button
              key={m.key}
              className={mode === m.key ? "active" : ""}
              onClick={() => setMode(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
        {took !== null && (
          <span className="pill">
            {hits.length} hits · <b>{took} ms</b>
          </span>
        )}
      </div>

      {error && <div className="empty">Error: {error}</div>}

      {searched && !error && hits.length === 0 && (
        <div className="empty">No matches yet. The index fills as changes stream in.</div>
      )}

      {hits.map((hit) => (
        <div className="hit" key={hit.id}>
          <div className="title">
            {hit.document.title_url ? (
              <a href={hit.document.title_url} target="_blank" rel="noreferrer">
                {hit.document.title}
              </a>
            ) : (
              hit.document.title
            )}
          </div>
          <div className="meta">
            <span className="badge score">score {hit.score.toFixed(4)}</span>
            {hit.bm25_rank != null && (
              <span className="badge bm25">BM25 #{hit.bm25_rank}</span>
            )}
            {hit.knn_rank != null && (
              <span className="badge knn">kNN #{hit.knn_rank}</span>
            )}
            <span>{hit.document.wiki}</span>
            {hit.document.edit_count != null && (
              <span>· {hit.document.edit_count} edits</span>
            )}
            {hit.document.last_user && <span>· {hit.document.last_user}</span>}
          </div>
          {hit.document.last_comment && (
            <div className="meta">{hit.document.last_comment}</div>
          )}
        </div>
      ))}
    </div>
  );
}
