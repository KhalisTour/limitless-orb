import { PageHero, ProgressBar, Section, StatusPill } from "../components/Primitives";
import { roadmapPhases } from "../data/courseContent";

export default function Roadmap() {
  return (
    <>
      <PageHero eyebrow="8-Week Campaign Map" title="Action first. Complexity later." subtitle="Weeks 1–3 establish applications as a daily routine. Outreach is the first nuance layer. Interview competence is the second." />
      <Section title="Course roadmap" subtitle="Not academic. Not passive. Every week ships action into the market.">
        <div className="roadmap-grid">
          {roadmapPhases.map((phase) => (
            <article className="roadmap-card" key={phase.id}>
              <div className="split-row"><strong>{phase.weeks}</strong><StatusPill tone={phase.status === "Active" ? "lime" : "purple"}>{phase.status}</StatusPill></div>
              <span className="phase-label">{phase.phase}</span>
              <h2>{phase.theme}</h2>
              <p>{phase.description}</p>
              <ProgressBar label="Completion" value={phase.completion} max={100} />
              <div className="deliverable-list">{phase.deliverables.map((item) => <span key={item}>{item}</span>)}</div>
              <div className="reward-box">Unlock: <strong>{phase.badge}</strong><small>{phase.reward}</small></div>
            </article>
          ))}
        </div>
      </Section>
    </>
  );
}
