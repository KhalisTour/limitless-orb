import { Link } from "react-router-dom";
import { badges, dailyChecklist, repOptions, statCards } from "../data/courseContent";
import { Card, CoachPanel, PageHero, ProgressBar, Section, StatGrid, StatusPill } from "../components/Primitives";
import { JobCaptureModal, PipelineCommandCenter, RecoveryModeCard, RoleMatchScorer } from "../components/CareerCommand";
import { sampleCapturedJobs } from "../data/jobCaptureData";

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
          <Link className="button secondary full" to="/resume-lab">Upload resume / open Resume Lab</Link>
        </Card>
      </section>

      <Section title="Capture external roles" subtitle="Paste a role, score it, and convert browsing into execution."><JobCaptureModal inline /></Section>

      <Section title="Pipeline Command Center" subtitle="Every metric includes an interpretation and next action."><PipelineCommandCenter /></Section>

      <Section title="High-match saved roles" subtitle="Top roles worth action now."><div className="card-grid three">{sampleCapturedJobs.map(job => <Card key={job.id}><h3>{job.role}</h3><p>{job.company} · {job.lane}</p><RoleMatchScorer score={{ matchScore: job.matchScore, nextAction: job.nextAction, reason: "Saved role with clear proof match." }} /></Card>)}</div></Section>

      <RecoveryModeCard />

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
