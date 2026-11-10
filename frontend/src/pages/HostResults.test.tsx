import { createElement, act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import HostResults from "./HostResults";
import type { AnalyticsPayload, MatchResult } from "../types/api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("../components/charts/PullsTimeline", () => ({ default: () => null }));
beforeEach(() => {
  document.body.innerHTML = "";
  sessionStorage.setItem("cb.hostToken", "T");
});

const team = { players: 1, totalPulls: 0, avgPullsPerPlayer: 0, bestPlayer: null, worstPlayer: null };
const analytics = (result: MatchResult | null): AnalyticsPayload => ({
  result, endReason: result === null ? null : result === "DRAW" ? "timeout" : "win", durationS: 30, totalPlayers: 2,
  playersPerTeam: { inter: 1, gremio: 1 }, totalPulls: { overall: 0, inter: 0, gremio: 0 },
  pullsPerSecond: [], peak: { value: 0, t: 0 }, finalRope: 0, teams: { inter: team, gremio: team }, players: [], leaderboard: [],
});

async function render(result: MatchResult | null, a: AnalyticsPayload = analytics(result)) {
  const host = document.createElement("div");
  document.body.append(host);
  await act(async () =>
    createRoot(host).render(
      createElement(MemoryRouter, { initialEntries: [{ pathname: "/host/results/m1", state: { analytics: a } }] },
        createElement(Routes, null, createElement(Route, { path: "/host/results/:id", element: createElement(HostResults) }))),
    ),
  );
  return host;
}
const srcs = (h: HTMLElement) => [...h.querySelectorAll<HTMLImageElement>(".results-mascots .pod-mascot")].map((i) => i.src);

it("winner happy, loser crying, banner copy", async () => {
  const h = await render("INTER");
  expect(h.querySelector(".result-banner")?.textContent).toContain("INTER VENCEU!");
  const [i, g] = srcs(h);
  expect(i).toMatch(/saci_happy/);
  expect(g).toMatch(/mosq_cry/);
});

it("draw: both idle", async () => {
  const h = await render("DRAW");
  expect(h.querySelector(".result-banner")?.textContent).toContain("EMPATE!");
  expect(srcs(h).map((s) => /master/.test(s))).toEqual([true, true]);
});

it("cancelled: no confetti, cancelled copy", async () => {
  const h = await render(null);
  expect(h.querySelector(".confetti")).toBeNull();
  expect(h.querySelector(".result-banner")?.textContent).toContain("PARTIDA CANCELADA");
});

it("win and draw show confetti", async () => {
  expect((await render("GREMIO")).querySelector(".confetti")).not.toBeNull();
  expect((await render("DRAW")).querySelector(".confetti")).not.toBeNull();
});

it("shows the MVP of each team with pulls", async () => {
  const p = (name: string, t: "inter" | "gremio", pulls: number) => ({ playerId: name, name, team: t, pulls, pctOfTeam: 50, pctOfAll: 25, pullsPerSec: 1 });
  const a = analytics("INTER");
  a.teams = { inter: { ...team, bestPlayer: p("Ana", "inter", 40) }, gremio: { ...team, bestPlayer: p("Beto", "gremio", 12) } };
  const h = await render("INTER", a);
  const mvps = [...h.querySelectorAll(".mvp")].map((m) => m.textContent);
  expect(mvps[0]).toContain("Ana");
  expect(mvps[0]).toContain("40 puxadas");
  expect(mvps[1]).toContain("Beto");
});

it("final rope is shown as a rounded integer", async () => {
  const h = await render("INTER", { ...analytics("INTER"), finalRope: -7.7333333333333325 });
  expect(h.textContent).toContain("-8");
  expect(h.textContent).not.toContain("7.73");
});
