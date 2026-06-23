import { Link, NavLink } from "react-router-dom";
import { navigation, userProgress } from "../data/courseContent";

export function AppShell({ children, headerAction }) {
  return (
    <div className="app-shell">
      <header className="site-header">
        <Link to="/" className="brand-mark" aria-label="Operation Exit home">
          <span className="brand-icon">🍍</span>
          <span><strong>Operation Exit</strong><small>career-transition command center</small></span>
        </Link>
        <nav className="nav-links" aria-label="Primary navigation">
          {navigation.map((item) => (
            <NavLink key={item.path} to={item.path} className={({ isActive }) => isActive ? "active" : undefined}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="header-actions">{headerAction}<div className="header-status"><span className="pulse" /> Pipeline Active</div></div>
      </header>
      {children}
    </div>
  );
}

export function PageHero({ eyebrow, title, subtitle, children }) {
  return (
    <section className="page-hero">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p className="hero-copy">{subtitle}</p>
      </div>
      {children && <div className="hero-side">{children}</div>}
    </section>
  );
}

export function Section({ title, subtitle, children, className = "" }) {
  return (
    <section className={`section ${className}`}>
      <div className="section-heading">
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

export function Card({ children, className = "", accent }) {
  return <article className={`card ${accent ? `accent-${accent}` : ""} ${className}`}>{children}</article>;
}

export function ProgressBar({ label = "Weekly XP", value = userProgress.weeklyXp, max = userProgress.weeklyXpGoal }) {
  const percent = Math.min(100, Math.round((value / max) * 100));
  return (
    <div className="progress-block" aria-label={`${label}: ${value} of ${max}`}>
      <div className="progress-label"><span>{label}</span><strong>{value} / {max}</strong></div>
      <div className="progress-track"><div className="progress-fill" style={{ width: `${percent}%` }} /></div>
    </div>
  );
}

export function StatGrid({ stats }) {
  return <div className="stat-grid">{stats.map((stat) => <div className="stat-card" key={stat.label}><strong>{stat.value}</strong><span>{stat.label}</span><small>{stat.hint}</small></div>)}</div>;
}

export function MissionPanel() {
  return (
    <Card className="mission-panel">
      <div className="split-row"><span className="muted">Current Mission</span><span className="pill">Week {userProgress.currentWeek}</span></div>
      <h2>{userProgress.currentMission}</h2>
      <p>{userProgress.missionDetail}</p>
      <ProgressBar />
      <div className="mini-metrics">
        <span>{userProgress.streak} day streak</span>
        <span>{userProgress.optionsCreated} options</span>
        <span>{userProgress.followUpsDue} follow-ups</span>
      </div>
    </Card>
  );
}

export function CoachPanel({ children }) {
  return <section className="coach-panel"><span>Coach Panel</span><p>{children}</p></section>;
}

export function StatusPill({ children, tone = "cyan" }) {
  return <span className={`status-pill ${tone}`}>{children}</span>;
}
