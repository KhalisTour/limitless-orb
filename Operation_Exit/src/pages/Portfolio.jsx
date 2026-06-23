import { portfolioProjects } from "../data/courseContent";
import { Card, PageHero, Section } from "../components/Primitives";

export default function Portfolio() {
  return (
    <>
      <PageHero eyebrow="Portfolio / Proof" title="Package the evidence." subtitle="Proof of work should make systems thinking obvious before an interview has to guess." />
      <Section title="Portfolio website" subtitle="Placeholder for the public proof hub.">
        <Card className="link-card"><span className="tag">Website</span><h3>https://your-portfolio.example</h3><p>Use this page as the source of truth for project links, case studies, and interview talk tracks.</p></Card>
      </Section>
      <Section title="Project proof cards" subtitle="Each project maps problem, system, tools, outcome, and interview talking point.">
        <div className="card-grid two">
          {portfolioProjects.map((project) => (
            <Card className="project-card" key={project.title}>
              <h3>{project.title}</h3>
              <dl>
                <div><dt>Problem</dt><dd>{project.problem}</dd></div>
                <div><dt>System built or improved</dt><dd>{project.system}</dd></div>
                <div><dt>Tools used</dt><dd>{project.tools}</dd></div>
                <div><dt>Measurable outcome</dt><dd>{project.outcome}</dd></div>
              </dl>
              <div className="talking-point">Interview talking point: <strong>{project.talkingPoint}</strong></div>
            </Card>
          ))}
        </div>
      </Section>
    </>
  );
}
