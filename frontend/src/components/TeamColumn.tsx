import type { RosterPlayer, TeamId } from "../types/api";
import { Crest } from "./Crest";
import PlayerIcon from "./PlayerIcon";

const NAMES: Record<TeamId, string> = { inter: "Inter", gremio: "Grêmio" };

export default function TeamColumn({ team, players }: { team: TeamId; players: RosterPlayer[] }) {
  return (
    <section className={`team-column ${team}`}>
      <header className="team-head">
        <Crest team={team} size={64} />
        <h2>{NAMES[team]}</h2>
        <p className="team-count">{players.length} {players.length === 1 ? "jogador" : "jogadores"}</p>
      </header>
      <div className={`team-roster${players.length > 12 ? " many" : ""}`}>
        {players.map((p) => <PlayerIcon key={p.id} name={p.name} team={team} />)}
      </div>
    </section>
  );
}
