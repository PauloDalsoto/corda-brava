import type { AnalyticsPayload, PlayerStats, TeamId } from "../types/api";
import { Crest } from "./Crest";
import { Icon, type IconName } from "./Icon";
import { PixelPanel } from "./PixelPanel";
import PullsTimeline from "./charts/PullsTimeline";

const NAME: Record<TeamId, string> = { inter: "Inter", gremio: "Grêmio" };
const TEAMS: TeamId[] = ["inter", "gremio"];
const TONE = { inter: "red", gremio: "blue" } as const;

const Tile = ({ icon, label, value }: { icon: IconName; label: string; value: string | number }) => (
  <PixelPanel className="stat-tile">
    <Icon name={icon} size={32} />
    <span>{label}</span>
    <strong>{value}</strong>
  </PixelPanel>
);
const Row = ({ label, value }: { label: string; value: string | number }) => (
  <div className="team-row"><span>{label}</span><strong>{value}</strong></div>
);
const Mvp = ({ p }: { p: PlayerStats | null }) => (
  <div className="mvp">
    <Icon name="crown" size={32} />
    <div><small>MVP</small><strong>{p ? p.name : "—"}</strong>{p && <em>{p.pulls} puxadas</em>}</div>
  </div>
);

export default function AnalyticsGrid({ a }: { a: AnalyticsPayload }) {
  const flag: IconName = a.finalRope < 0 ? "pennant-red" : a.finalRope > 0 ? "pennant-blue" : "whistle";
  return (
    <section className="analytics">
      <div className="stat-tiles">
        <Tile icon="rope-coil" label="Puxadas totais" value={a.totalPulls.overall} />
        <Tile icon="hourglass" label="Duração" value={`${a.durationS}s`} />
        <Tile icon="lightning" label="Pico" value={`${a.peak.value}/s aos ${a.peak.t}s`} />
        <Tile icon={flag} label="Corda final" value={Math.round(a.finalRope)} />
      </div>
      <div className="team-cards">
        {TEAMS.map((t) => (
          <PixelPanel key={t} tone={TONE[t]} className={`team-card ${t}`}>
            <h3><Crest team={t} size={32} /> {NAME[t]}</h3>
            <Row label="Jogadores" value={a.playersPerTeam[t]} />
            <Row label="Puxadas" value={a.totalPulls[t]} />
            <Row label="Média por jogador" value={a.teams[t].avgPullsPerPlayer.toFixed(1)} />
            <Mvp p={a.teams[t].bestPlayer} />
          </PixelPanel>
        ))}
      </div>
      <PixelPanel className="chart-panel">
        <h2>Puxadas por segundo</h2>
        <PullsTimeline data={a.pullsPerSecond} />
      </PixelPanel>
      {(
        <PixelPanel className="ranking-panel">
          <h2>Ranking</h2>
          <ol className="leaderboard">
            <li className="rk-head" aria-hidden="true"><span>#</span><span /><span>NOME</span><span>PUXADAS</span><span>% TOTAL</span><span>/S</span></li>
            {a.leaderboard.slice(0, 5).map((p, i) => (
              <li key={p.playerId} className={p.team}>
                <span>{i + 1}</span>
                <i className="rk-dot" />
                <span className="rk-name">{p.name}</span>
                <span>{p.pulls}</span>
                <span>{p.pctOfAll}%</span>
                <span>{p.pullsPerSec}</span>
              </li>
            ))}
          </ol>
        </PixelPanel>
      )}
    </section>
  );
}
