"use client";

import { useEffect, useRef, useState } from "react";
import { LiveEvent, WS_URL } from "@/lib/api";

const MAX_EVENTS = 60;

export default function LiveFeed({
  onConnectedChange,
}: {
  onConnectedChange: (connected: boolean) => void;
}) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closedByUs = false;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    const connect = () => {
      const ws = new WebSocket(`${WS_URL}/ws/live`);
      wsRef.current = ws;

      ws.onopen = () => onConnectedChange(true);
      ws.onclose = () => {
        onConnectedChange(false);
        if (!closedByUs) reconnectTimer = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (msg) => {
        try {
          const event: LiveEvent = JSON.parse(msg.data);
          if (!event.title) return;
          setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));
        } catch {
          /* ignore malformed frames */
        }
      };
    };

    connect();
    return () => {
      closedByUs = true;
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, [onConnectedChange]);

  return (
    <div className="card">
      <h2>Live change stream</h2>
      {events.length === 0 ? (
        <div className="empty">Waiting for change events from the CDC pipeline…</div>
      ) : (
        <div className="feed">
          {events.map((e, i) => (
            <div className="event" key={`${e.id}-${e.ts_ms}-${i}`}>
              <span className={`op ${(e.op || "r").toLowerCase()}`}>
                {(e.op || "r").toUpperCase()}
              </span>
              <span className="etitle" title={e.title}>
                {e.title_url ? (
                  <a href={e.title_url} target="_blank" rel="noreferrer">
                    {e.title}
                  </a>
                ) : (
                  e.title
                )}
              </span>
              <span className="ewiki">{e.wiki}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
