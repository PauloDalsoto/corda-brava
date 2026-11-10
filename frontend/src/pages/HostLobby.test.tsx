import { createElement, act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import * as sock from "../hooks/useHostSocket";
import HostLobby from "./HostLobby";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  document.body.innerHTML = "";
  sessionStorage.setItem("cb.hostToken", "T");
});

it("QR overlay shows the snapshot joinUrl", async () => {
  const match = { id: "m1", status: "LOBBY", roster: { inter: [], gremio: [] }, joinUrl: "http://lan:5173/j/m1" };
  vi.spyOn(sock, "useHostSocket").mockReturnValue({
    status: "LOBBY", snapshot: match as never, lastTick: null, ended: null, terminal: null, connected: true, authError: false,
  });
  const host = document.createElement("div");
  document.body.append(host);
  await act(async () =>
    createRoot(host).render(
      createElement(
        MemoryRouter,
        { initialEntries: ["/host/lobby/m1"] },
        createElement(Routes, null, createElement(Route, { path: "/host/lobby/:id", element: createElement(HostLobby) })),
      ),
    ),
  );
  expect(host.querySelector(".join-url")?.textContent).toBe("http://lan:5173/j/m1");
  await act(async () => host.querySelector<HTMLButtonElement>(".qr-btn")!.click());
  const overlay = document.body.querySelector(".qr-overlay");
  expect(overlay?.parentElement).toBe(document.body);
  expect(overlay?.textContent).toContain("http://lan:5173/j/m1");
});

async function mountLobby(roster: { inter: unknown[]; gremio: unknown[] }) {
  vi.spyOn(sock, "useHostSocket").mockReturnValue({
    status: "LOBBY", snapshot: { id: "m1", status: "LOBBY", roster, joinUrl: "http://x/j/m1" } as never, lastTick: null, ended: null, terminal: null, connected: true, authError: false,
  });
  const host = document.createElement("div");
  document.body.append(host);
  await act(async () =>
    createRoot(host).render(
      createElement(
        MemoryRouter,
        { initialEntries: ["/host/lobby/m1"] },
        createElement(Routes, null, createElement(Route, { path: "/host/lobby/:id", element: createElement(HostLobby) })),
      ),
    ),
  );
  return host;
}
const startBtn = (h: HTMLElement) => h.querySelector<HTMLButtonElement>(".start-btn")!;

it("start is disabled with no players", async () => {
  const host = await mountLobby({ inter: [], gremio: [] });
  expect(startBtn(host).disabled).toBe(true);
  expect(host.textContent).not.toContain("Um time está sem jogadores");
});

it("warns when one team is empty but still allows start", async () => {
  const host = await mountLobby({ inter: [{ id: "p1", name: "Ana" }], gremio: [] });
  expect(host.textContent).toContain("Um time está sem jogadores");
  expect(startBtn(host).disabled).toBe(false);
});
