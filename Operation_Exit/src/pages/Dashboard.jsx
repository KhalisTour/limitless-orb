import { Link } from "react-router-dom";
import { badges, dailyChecklist, repOptions, statCards } from "../data/courseContent";
import { Card, CoachPanel, PageHero, ProgressBar, Section, StatGrid, StatusPill } from "../components/Primitives";

export default function Dashboard() {
  return (
    <>
      <PageHero eyebrow="Main Command Center" title="Complete today’s rep." subtitle="Clear next action, visible progress, and one-click recovery when energy is low.">
        <Link className="button primary" to="/todays-rep">Start the 20-minute rep</Link>
      </PageHero>

      <section className="dashboard-grid">
        <Card className="priority-card">
          <span className="tag">Required Today</span>
          <h2>One exit rep before optimization.</h2>
          <p>Minimum viable action. No drama. No identity spiral. Next play.</p>
          <div className="timer-card"><span>Timer</span><strong>20:00</strong></div>
          <div className="rep-stack">
            {repOptions.map((rep) => <Link className={rep.recovery ? "rep-choice recovery" : "rep-choice"} to="/todays-rep" key={rep.id}>{rep.title}<small>+{rep.xp} XP</small></Link>)}
          </div>
        </Card>

        <Card>
          <h2>Weekly progress</h2>
          <ProgressBar />
          <StatGrid stats={statCards} />
          <Link className="button secondary full" to="/tracker">Quick link: application tracker</Link>
        </Card>
      </section>

      <Section title="Daily checklist" subtitle="Make it obvious, easy, attractive, and satisfying.">
        <div className="checklist-grid">
          {dailyChecklist.map((item) => <Card className={item.done ? "complete" : ""} key={item.label}><StatusPill tone={item.done ? "lime" : "purple"}>{item.done ? "Done" : "Next"}</StatusPill><h3>{item.label}</h3></Card>)}
        </div>
      </Section>

      <Section title="Badges & unlocks" subtitle="Progress should feel visible without becoming noisy.">
        <div className="card-grid four">
          {badges.map((badge) => <Card key={badge.name} className={`badge-card ${badge.status}`}><span className="badge-medal">🏅</span><h3>{badge.name}</h3><p>{badge.description}</p><StatusPill>{badge.status}</StatusPill></Card>)}
        </div>
      </Section>

      <CoachPanel>Missed a day? Run the recovery version. One rep counts. The streak is a tool, not a courtroom.</CoachPanel>
    </>
  );
}
