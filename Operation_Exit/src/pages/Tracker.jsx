import { applications } from "../data/courseContent";
import { Card, PageHero, Section, StatusPill } from "../components/Primitives";
import { JobCaptureModal, ResumeTailorPanel, RoleMatchScorer } from "../components/CareerCommand";

const statusTone = { Saved: "purple", Applied: "cyan", "Followed Up": "cyan", "Phone Screen": "lime", Interview: "lime", "Final Round": "yellow", Rejected: "coral", Offer: "yellow" };

export default function Tracker() {
  return (
    <>
      <PageHero eyebrow="Application Pipeline" title="No vague fog." subtitle="Every role gets a lane, status, deadline, notes, and next action."><JobCaptureModal /></PageHero>
      <Section title="Visible pipeline" subtitle="Cards over spreadsheets. Each card answers: what happens next?">
        <div className="pipeline-list">
          {applications.map((app) => (
            <Card className="application-card" key={app.id}>
              <div>
                <h3>{app.role}</h3>
                <p>{app.company}</p>
              </div>
              <span>{app.lane}</span>
              <StatusPill tone={statusTone[app.status]}>{app.status}</StatusPill>
              <div className="next-action"><small>Next action</small><strong>{app.nextAction}</strong></div>
              <div><small>Deadline</small><strong>{app.deadline}</strong></div>
              <p className="notes">{app.notes}</p><div className="notes"><RoleMatchScorer score={{ matchScore: app.id === 1 ? 88 : 82, nextAction: app.nextAction, reason: app.notes }} /><ResumeTailorPanel job={app} /></div>
            </Card>
          ))}
        </div>
      </Section>
    </>
  );
}
