import { interviewDrills, interviewModules } from "../data/courseContent";
import { Card, PageHero, ProgressBar, Section, StatusPill } from "../components/Primitives";

export default function InterviewGym() {
  const practiced = interviewDrills.filter((drill) => drill.practiced).length;
  return (
    <>
      <PageHero eyebrow="Interview Gym" title="Conversion is trained." subtitle="Practice structure, warmth, proof, and judgment until answers feel usable under pressure." />
      <Section title="Training modules" subtitle="This is not academic. These are reps.">
        <div className="card-grid three">{interviewModules.map((module) => <Card key={module}><h3>{module}</h3><p>Build answer control with Point → Evidence → Judgment.</p></Card>)}</div>
      </Section>
      <Section title="Mock drills" subtitle={`${practiced} of ${interviewDrills.length} answers practiced.`}>
        <ProgressBar label="Answer reps" value={practiced} max={interviewDrills.length} />
        <div className="drill-list">{interviewDrills.map((drill) => <Card className={drill.practiced ? "complete drill-card" : "drill-card"} key={drill.prompt}><StatusPill tone={drill.practiced ? "lime" : "purple"}>{drill.practiced ? "Practiced" : "Queued"}</StatusPill><h3>{drill.prompt}</h3></Card>)}</div>
      </Section>
    </>
  );
}
