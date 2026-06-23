import { Link } from "react-router-dom";
import { Card, CoachPanel, MissionPanel, Section, StatGrid } from "../components/Primitives";
import { missionCards, statCards } from "../data/courseContent";

export default function Home() {
  return (
    <>
      <section className="home-hero">
        <div className="hero-content">
          <span className="eyebrow">Training Camp for Career Escape Velocity</span>
          <h1>Build options.<br />Create leverage.<br />Exit with control.</h1>
          <p className="hero-copy">An 8-week action-first course built to turn job-search avoidance into repeatable daily reps, real applications, stronger interviews, and a live exit pipeline.</p>
          <div className="button-row">
            <Link className="button primary" to="/todays-rep">Start Today’s Rep</Link>
            <Link className="button secondary" to="/roadmap">Open Course Map</Link>
          </div>
        </div>
        <MissionPanel />
      </section>

      <Section title="Mission Control" subtitle="Open the page. Know the next move. Complete the rep.">
        <div className="card-grid three">
          {missionCards.map((mission) => (
            <Link to={mission.route} key={mission.id} className="card-link">
              <Card accent={mission.accent}>
                <span className="tag">{mission.label}</span>
                <h3>{mission.title}</h3>
                <p>{mission.description}</p>
              </Card>
            </Link>
          ))}
        </div>
      </Section>

      <Section title="Proof of motion" subtitle="The system rewards visible action instead of heroic overhauls.">
        <StatGrid stats={statCards} />
      </Section>

      <CoachPanel>Chest up. Base under you. First rep. The goal is not to escape in one heroic move. The goal is to create options every day until pressure decreases.</CoachPanel>
    </>
  );
}
