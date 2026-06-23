import { useState } from "react";
import { repOptions } from "../data/courseContent";
import { Card, CoachPanel, PageHero, StatusPill } from "../components/Primitives";
import { useRepCompletion } from "../hooks/useRepCompletion";
import { RecoveryModeCard } from "../components/CareerCommand";

export default function TodaysRep() {
  const [selectedRep, setSelectedRep] = useState(repOptions[0]);
  const { completed, completedRep, xpClaimed, completeRep, resetRep } = useRepCompletion();

  return (
    <>
      <PageHero eyebrow="Behavior Engine" title="One rep counts." subtitle="Choose the smallest useful action. Twenty minutes is enough to create motion.">
        <StatusPill tone={completed ? "lime" : "cyan"}>{completed ? `Completed: ${completedRep}` : "Ready"}</StatusPill>
      </PageHero>

      <section className="rep-page-grid">
        <Card className={completed ? "completion-card complete" : "completion-card"}>
          <span className="tag">20-Minute Rep</span>
          <div className="big-timer">20:00</div>
          <h2>{selectedRep.title}</h2>
          <p>{selectedRep.detail}</p>
          <button className="button primary" onClick={() => completeRep(selectedRep)}>Complete Rep + Claim {selectedRep.xp} XP</button>
          {completed && <div className="celebration">🎉 Rep complete. +{xpClaimed} XP. Options created. Chest up.</div>}
          {completed && <button className="button ghost" onClick={resetRep}>Reset demo state</button>}
        </Card>

        <Card>
          <h2>Choose today’s rep</h2>
          <p className="muted">Applications create motion. Outreach creates leverage. Interviews create conversion.</p>
          <div className="rep-stack">
            {repOptions.map((rep) => (
              <button className={`${selectedRep.id === rep.id ? "selected" : ""} ${rep.recovery ? "recovery" : ""} rep-choice`} onClick={() => setSelectedRep(rep)} key={rep.id}>
                {rep.title}<small>{rep.detail} · +{rep.xp} XP</small>
              </button>
            ))}
          </div>
        </Card>
      </section>

      <RecoveryModeCard />

      <CoachPanel>Too tired? Run the recovery version. Open the tracker, choose one next action, and stop while the system is still intact.</CoachPanel>
    </>
  );
}
