"use client";

import { useEffect, useState } from "react";
import { readiness, Readiness } from "@/lib/api";

export default function StatusBar({ wsConnected }: { wsConnected: boolean }) {
  const [ready, setReady] = useState<Readiness | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const r = await readiness();
        if (active) setReady(r);
      } catch {
        if (active) setReady(null);
      }
    };
    poll();
    const id = setInterval(poll, 4000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="statusbar">
      <span className="pill">
        <span className={`led ${ready?.elasticsearch ? "on" : "off"}`} />
        Elasticsearch
      </span>
      <span className="pill">
        <span className={`led ${wsConnected ? "on" : "off"}`} />
        Live stream
      </span>
      <span className="pill">
        <span className={`led ${ready?.llm ? "on" : "off"}`} />
        LLM
      </span>
      <span className="pill">
        Indexed <b>{ready ? ready.documents.toLocaleString() : "\u2014"}</b>
      </span>
      <a className="pill" href="http://localhost:3001" target="_blank" rel="noreferrer">
        Grafana &#8599;
      </a>
    </div>
  );
}
