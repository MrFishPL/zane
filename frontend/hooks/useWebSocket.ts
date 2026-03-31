"use client";

import { useEffect, useState, useRef, useCallback } from "react";

const WS_BASE =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

const MAX_RETRIES = 5;
const BASE_DELAY = 1000; // ms

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type WsCallback = (data: any) => void;

/**
 * WebSocket hook with auto-reconnect and exponential backoff.
 *
 * Connects to the backend WebSocket for the given conversationId.
 * On disconnect it will retry up to MAX_RETRIES times with exponential
 * backoff. Passing null as conversationId disconnects immediately.
 */
export function useWebSocket(
  conversationId: string | null,
  onMessage: WsCallback
) {
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  // Stable refs for values that need to be accessed in WS callbacks
  const onMessageRef = useRef(onMessage);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const setConnectedRef = useRef(setConnected);
  const setReconnectingRef = useRef(setReconnecting);

  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);
  useEffect(() => { setConnectedRef.current = setConnected; }, [setConnected]);
  useEffect(() => { setReconnectingRef.current = setReconnecting; }, [setReconnecting]);

  const teardown = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (wsRef.current) {
      const ws = wsRef.current;
      ws.onopen = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.close();
      wsRef.current = null;
    }
    retriesRef.current = 0;
  }, []);

  useEffect(() => {
    teardown();
    setConnected(false);
    setReconnecting(false);

    if (!conversationId) {
      return;
    }

    function openConnection(convId: string) {
      if (wsRef.current?.readyState === WebSocket.OPEN) return;

      const ws = new WebSocket(
        `${WS_BASE}/ws/conversations/${convId}`
      );
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectedRef.current(true);
        setReconnectingRef.current(false);
        retriesRef.current = 0;
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessageRef.current(data);
        } catch {
          onMessageRef.current(event.data);
        }
      };

      ws.onerror = () => {
        // onerror is always followed by onclose
      };

      ws.onclose = () => {
        setConnectedRef.current(false);
        wsRef.current = null;

        if (retriesRef.current < MAX_RETRIES) {
          setReconnectingRef.current(true);
          const delay =
            BASE_DELAY * Math.pow(2, retriesRef.current);
          retriesRef.current += 1;
          timerRef.current = setTimeout(() => {
            openConnection(convId);
          }, delay);
        } else {
          setReconnectingRef.current(false);
        }
      };
    }

    openConnection(conversationId);

    return () => {
      teardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  return { connected, reconnecting };
}
