"use client";

import { useState } from "react";
import StatusBar from "@/components/StatusBar";
import LiveFeed from "@/components/LiveFeed";
import SearchPanel from "@/components/SearchPanel";
import RagPanel from "@/components/RagPanel";

export default function Home() {
  const [wsConnected, setWsConnected] = useState(false);

  return (
    <div className="container">
      <header className="header">
        <div className="brand">
          <span className="dot" />
          <div>
            <h1>PulseSearch</h1>
            <span className="tag">
              Real-time CDC · Hybrid search · Grounded RAG
            </span>
          </div>
        </div>
        <StatusBar wsConnected={wsConnected} />
      </header>

      <div className="grid">
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <SearchPanel />
          <RagPanel />
        </div>
        <LiveFeed onConnectedChange={setWsConnected} />
      </div>

      <div className="footer">
        MySQL &rarr; Debezium &rarr; Kafka &rarr; Elasticsearch (BM25 + dense
        vectors) &rarr; FastAPI &rarr; WebSockets. Fully local, zero cost.
      </div>
    </div>
  );
}
