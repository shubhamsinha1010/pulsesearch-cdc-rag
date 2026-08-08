"use client";

import { useState } from "react";
import { ask, RAGAnswer } from "@/lib/api";

export default function RagPanel() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<RAGAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (question.trim().length < 3) return;
    setLoading(true);
    setError(null);
    try {
      setAnswer(await ask(question));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h2>Ask the live index (RAG)</h2>
      <div className="controls">
        <input
          type="text"
          placeholder="e.g. What topics are being edited right now?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <button onClick={run} disabled={loading}>
          {loading ? <span className="spinner" /> : "Ask"}
        </button>
      </div>

      {error && <div className="empty">Error: {error}</div>}

      {answer && (
        <>
          <div className="answer">{answer.answer}</div>
          {!answer.grounded && (
            <div className="freshness">
              Not grounded — no relevant indexed context was found.
            </div>
          )}
          {answer.citations.length > 0 && (
            <div className="citations">
              <strong style={{ fontSize: 12.5 }}>Sources</strong>
              {answer.citations.map((c, i) => (
                <div className="citation" key={c.id}>
                  [{i + 1}]{" "}
                  {c.title_url ? (
                    <a href={c.title_url} target="_blank" rel="noreferrer">
                      {c.title}
                    </a>
                  ) : (
                    c.title
                  )}{" "}
                  · {c.wiki}
                  {c.event_time
                    ? ` · ${new Date(c.event_time).toLocaleTimeString()}`
                    : ""}
                </div>
              ))}
            </div>
          )}
          {answer.freshest_source && (
            <div className="freshness">
              Freshest source: {new Date(answer.freshest_source).toLocaleString()}
            </div>
          )}
        </>
      )}
    </div>
  );
}
