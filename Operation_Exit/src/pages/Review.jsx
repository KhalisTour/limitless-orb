import { reviewItems } from "../data/courseContent";
import { Card, CoachPanel, PageHero, Section } from "../components/Primitives";

export default function Review() {
  return (
    <>
      <PageHero eyebrow="Pipeline Review" title="Optimize the next week." subtitle="Review applications, outreach, interviews, bottlenecks, follow-ups, second rounds, and decisions." />
      <Section title="Weekly review board" subtitle="No vague anxiety. Convert the week into next actions.">
        <div className="card-grid four">
          {reviewItems.map((item) => <Card key={item.label}><span className="tag">{item.label}</span><h3>{item.value}</h3><p>{item.note}</p></Card>)}
        </div>
      </Section>
      <CoachPanel>Do not optimize for relief only. Score each option by commute, manager quality, growth, stability, compensation, and whether the work creates better future options.</CoachPanel>
    </>
  );
}
