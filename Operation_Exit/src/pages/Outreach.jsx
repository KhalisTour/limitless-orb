import { outreachContacts, outreachLanes, outreachScripts } from "../data/courseContent";
import { Card, PageHero, Section, StatusPill } from "../components/Primitives";

export default function Outreach() {
  return (
    <>
      <PageHero eyebrow="Leverage Layer" title="Warm paths beat cold fog." subtitle="Add outreach only after the application routine exists. Keep messages short and track the follow-up." />
      <Section title="Outreach lanes" subtitle="Pick one lane, send one message, log one follow-up.">
        <div className="card-grid five">{outreachLanes.map((lane) => <Card key={lane}><h3>{lane}</h3><p>One short message. One follow-up date. No overthinking.</p></Card>)}</div>
      </Section>
      <Section title="Scripts" subtitle="Use these as launch rails, then personalize lightly.">
        <div className="script-grid">{outreachScripts.map((script) => <Card key={script.title}><span className="tag">Script</span><h3>{script.title}</h3><p>{script.body}</p></Card>)}</div>
      </Section>
      <Section title="Outreach tracker" subtitle="Leverage becomes real when the next action is visible.">
        <div className="contact-list">{outreachContacts.map((contact) => <Card className="contact-card" key={contact.contact}><h3>{contact.contact}</h3><span>{contact.platform}</span><span>{contact.relationship}</span><StatusPill tone={contact.sent === "Yes" ? "lime" : "purple"}>{contact.sent === "Yes" ? "Sent" : "Draft"}</StatusPill><span>{contact.followUp}</span><strong>{contact.outcome}</strong></Card>)}</div>
      </Section>
    </>
  );
}
