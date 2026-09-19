import { useCallback, useEffect, useState } from "react";
import { api, base, refreshData } from "../services/api";
export function useApi<T>(path: string) {
  const [data, setData] = useState<T>();
  const [loadedPath, setLoadedPath] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true,
      running = false;
    async function load() {
      if (running) return;
      running = true;
      try {
        const result = await api<T>(path);
        if (active) {
          setData(result);
          setLoadedPath(path);
          setError("");
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Request failed");
      } finally {
        running = false;
        if (active) setLoading(false);
      }
    }
    void load();
    const listener = () => {
      void load();
    };
    window.addEventListener("autopilot:refresh", listener);
    const timer = window.setInterval(listener, 30000);
    return () => {
      active = false;
      window.removeEventListener("autopilot:refresh", listener);
      window.clearInterval(timer);
    };
  }, [path]);
  return {
    data: loadedPath === path ? data : undefined,
    error,
    loading: loading || loadedPath !== path,
  };
}
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const run = useCallback(
    async (fn: () => Promise<unknown>, success = "Request accepted") => {
      setBusy(true);
      setError("");
      setMessage("");
      try {
        await fn();
        setMessage(success);
        refreshData();
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Request failed");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [],
  );
  return { busy, error, message, run };
}
export function useLive() {
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket;
    let timer: number;
    function connect() {
      const url = new URL(base || window.location.origin);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.pathname = "/api/ws";
      socket = new WebSocket(url);
      socket.onopen = () => {
        setConnected(true);
        refreshData();
      };
      socket.onmessage = (e) => {
        try {
          const event = JSON.parse(String(e.data));
          if (event.events?.length) refreshData();
        } catch {
          /* Ignore malformed notifications; REST remains authoritative */
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!disposed) timer = window.setTimeout(connect, 5000);
      };
      socket.onerror = () => socket.close();
    }
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      socket.close();
    };
  }, []);
  return connected;
}
