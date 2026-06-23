export const navigation = [
  { path: "/", label: "Home" },
  { path: "/dashboard", label: "Dashboard" },
  { path: "/roadmap", label: "Roadmap" },
  { path: "/todays-rep", label: "Today’s Rep" },
  { path: "/tracker", label: "Tracker" },
  { path: "/outreach", label: "Outreach" },
  { path: "/interview-gym", label: "Interview Gym" },
  { path: "/portfolio", label: "Proof" },
  { path: "/review", label: "Review" }
];

export const userProgress = {
  currentWeek: 1,
  currentMission: "Complete 1 Exit Rep",
  missionDetail: "20 minutes. One application, one saved job, one outreach message, or one interview answer.",
  weeklyXp: 420,
  weeklyXpGoal: 700,
  weeklyCompletion: 60,
  optionsCreated: 7,
  streak: 4,
  applications: 12,
  outreach: 8,
  interviewReps: 5,
  followUpsDue: 5
};

export const statCards = [
  { label: "Day Streak", value: userProgress.streak, hint: "Keep the chain light, not fragile." },
  { label: "Applications", value: userProgress.applications, hint: "Motion creates signal." },
  { label: "Outreach", value: userProgress.outreach, hint: "Leverage layer active." },
  { label: "Interview Reps", value: userProgress.interviewReps, hint: "Conversion practice." },
  { label: "Options Created", value: userProgress.optionsCreated, hint: "Pressure decreasing." }
];

export const missionCards = [
  { id: "application", label: "Core Habit", title: "Daily Application Rep", description: "Weeks 1–3 make applications automatic before adding complexity.", accent: "yellow", route: "/todays-rep" },
  { id: "outreach", label: "Leverage", title: "Outreach Rep", description: "Alumni, former coworkers, hiring managers, warm intros, and recruiters.", accent: "cyan", route: "/outreach" },
  { id: "interview", label: "Conversion", title: "Interview Mechanics", description: "Concise answers, STAR stories, warmth signals, trust, and judgment.", accent: "purple", route: "/interview-gym" },
  { id: "portfolio", label: "Evidence", title: "Portfolio Proof", description: "Package project proof so interviews can see systems thinking fast.", accent: "orange", route: "/portfolio" },
  { id: "pipeline", label: "Control", title: "Pipeline Review", description: "Every role gets a status, next action, follow-up, and decision point.", accent: "blue", route: "/review" },
  { id: "reset", label: "No Shame Reset", title: "Recovery Version", description: "When exhausted, complete the smallest useful rep and protect the system.", accent: "coral", route: "/todays-rep" }
];

export const roadmapPhases = [
  {
    id: "build-rep",
    weeks: "Weeks 1–3",
    status: "Active",
    phase: "Build the Rep",
    theme: "Application Routine Foundation",
    description: "Make applications obvious, easy, attractive, and repeatable. Do not optimize before motion exists.",
    deliverables: ["Daily 20-minute rep", "Saved searches", "Application tracker", "First 30 applications"],
    badge: "Pipeline Builder",
    reward: "+300 XP and application autopilot unlocked",
    completion: 60
  },
  {
    id: "create-leverage",
    weeks: "Weeks 4–5",
    status: "Locked",
    phase: "Create Leverage",
    theme: "Outreach and Warm Intros",
    description: "Add relationship leverage after the base application habit is online.",
    deliverables: ["10 outreach messages", "Warm intro script", "LinkedIn system", "Follow-up cadence"],
    badge: "Warm Intro",
    reward: "Leverage lane unlocked",
    completion: 0
  },
  {
    id: "convert",
    weeks: "Weeks 6–7",
    status: "Locked",
    phase: "Convert",
    theme: "Interview Competence",
    description: "Turn interviews into trust through structure, warmth, judgment, and proof.",
    deliverables: ["5 STAR stories", "Mock interview reps", "Trust signal bank", "Role-specific prep"],
    badge: "Interview Ready",
    reward: "Interview gym advanced drills unlocked",
    completion: 0
  },
  {
    id: "close",
    weeks: "Week 8",
    status: "Locked",
    phase: "Close",
    theme: "Pipeline Optimization",
    description: "Manage second rounds, compensation, follow-ups, offers, and decisions.",
    deliverables: ["Pipeline review", "Follow-up scripts", "Offer scorecard", "Decision framework"],
    badge: "Offer Watch",
    reward: "Decision board unlocked",
    completion: 0
  }
];

export const repOptions = [
  { id: "apply", title: "Apply to one job", xp: 50, detail: "Tailor one role and submit it." },
  { id: "save", title: "Save one job", xp: 20, detail: "Add a promising role to the tracker." },
  { id: "outreach", title: "Send one outreach message", xp: 45, detail: "Use a script. Keep it short." },
  { id: "interview", title: "Practice one interview answer", xp: 35, detail: "Record or write one concise answer." },
  { id: "recovery", title: "Recovery rep", xp: 10, detail: "Too tired? Open tracker, pick next action, stop.", recovery: true }
];

export const dailyChecklist = [
  { label: "Open saved searches", done: true },
  { label: "Pick one role lane", done: true },
  { label: "Run a 20-minute rep", done: false },
  { label: "Log the next action", done: false },
  { label: "Claim XP", done: false }
];

export const badges = [
  { name: "First Rep", status: "earned", description: "You started instead of negotiating with the task." },
  { name: "Streak Starter", status: "earned", description: "Four days of useful motion." },
  { name: "Pipeline Builder", status: "active", description: "Build a visible list of options." },
  { name: "Warm Intro", status: "locked", description: "Unlocks in Weeks 4–5." },
  { name: "Interview Ready", status: "locked", description: "Unlocks after five practiced stories." },
  { name: "No Shame Reset", status: "earned", description: "Recovery reps keep the system alive." },
  { name: "Chest Up", status: "active", description: "Base under you. Next play." }
];

export const applications = [
  { id: 1, role: "Marketing Analyst", company: "Brightline Growth", lane: "Marketing Analyst", status: "Saved", nextAction: "Tailor résumé", deadline: "Today", notes: "Analytics-heavy stepping stone. Pull proof from funnel project." },
  { id: 2, role: "CRM Specialist", company: "Northstar Health", lane: "CRM / Lifecycle Marketing", status: "Applied", nextAction: "Follow up Friday", deadline: "Jun 26", notes: "Strong lifecycle fit. Mention segmentation and handoff cleanup." },
  { id: 3, role: "Product Ops Associate", company: "Atlas AI", lane: "Product Operations Associate", status: "Phone Screen", nextAction: "Prep 3 stories", deadline: "Jun 24", notes: "Emphasize systems thinking, ambiguity, and cross-functional execution." },
  { id: 4, role: "Revenue Ops Analyst", company: "KiteWorks", lane: "Revenue Operations / Sales Operations", status: "Followed Up", nextAction: "Send proof link", deadline: "Jun 27", notes: "Connect CRM improvements to revenue hygiene." },
  { id: 5, role: "Digital Marketing Analyst", company: "Orbit Labs", lane: "Digital Marketing Analyst", status: "Interview", nextAction: "Practice project walkthrough", deadline: "Jun 25", notes: "Use MLB predictor to show model communication." },
  { id: 6, role: "Performance Marketing Associate", company: "SignalWorks", lane: "Performance Marketing Associate", status: "Final Round", nextAction: "Prepare compensation notes", deadline: "Jun 28", notes: "Score role on commute, manager, growth, compensation." }
];

export const outreachLanes = ["Alumni", "Former coworkers", "Hiring managers", "Warm intros", "Recruiters"];
export const outreachScripts = [
  { title: "Short alumni message", body: "Hi — I’m moving toward analytics-heavy marketing/operator roles and saw your path. Could I ask two quick questions about how your team evaluates candidates?" },
  { title: "Former coworker message", body: "I’m building a focused exit pipeline and remembered how well you understood cross-functional teams. If you know of marketing ops or analyst openings, I’d value a pointer." },
  { title: "Hiring manager message", body: "I noticed the role emphasizes systems improvement. I’ve built dashboards and conversion workflows that made messy funnels easier to act on. Worth a quick conversation?" },
  { title: "Follow-up message", body: "Quick follow-up — still interested, and I can send a short proof-of-work link if useful." },
  { title: "Warm intro request", body: "Would you be comfortable introducing me to the hiring manager? I’ll send a concise blurb to make it easy." }
];

export const outreachContacts = [
  { contact: "Maya Chen", platform: "LinkedIn", relationship: "Alumni", sent: "Yes", followUp: "Jun 27", outcome: "Replied" },
  { contact: "Chris Patel", platform: "Email", relationship: "Former coworker", sent: "Yes", followUp: "Jun 25", outcome: "Intro offered" },
  { contact: "Dana Lee", platform: "LinkedIn", relationship: "Hiring manager", sent: "No", followUp: "Jun 26", outcome: "Draft ready" },
  { contact: "Jordan Smith", platform: "Email", relationship: "Recruiter", sent: "Yes", followUp: "Jun 28", outcome: "Waiting" }
];

export const interviewModules = ["Concise answers", "STAR stories", "Warmth signals", "Trust signals", "Character and judgment framing", "Point → Evidence → Judgment", "Role-specific prep"];
export const interviewDrills = [
  { prompt: "Tell me about yourself", practiced: true },
  { prompt: "Why this role?", practiced: true },
  { prompt: "Walk me through a project", practiced: true },
  { prompt: "Describe a time you improved a system", practiced: true },
  { prompt: "Describe a time you handled ambiguity", practiced: false },
  { prompt: "Why are you leaving your current role?", practiced: false },
  { prompt: "What makes you a strong fit?", practiced: false }
];

export const portfolioProjects = [
  { title: "Trading Terminal", problem: "Scattered market signals created decision fog.", system: "Signal dashboard and ranking workflow.", tools: "Python, FastAPI, SQLite", outcome: "Faster setup review and clearer trade selection.", talkingPoint: "I turn noisy data into executable decisions." },
  { title: "MLB Home Run Predictor", problem: "Hard to compare hitter upside quickly.", system: "Scoring model and visual radar.", tools: "React, analytics, feature scoring", outcome: "Clear ranked candidates with explainable signals.", talkingPoint: "I can communicate model output simply." },
  { title: "Leasing / EliseAI Conversion Improvements", problem: "Leads needed cleaner conversion paths and handoffs.", system: "Improved follow-up flows and CRM hygiene.", tools: "CRM, lifecycle ops, messaging QA", outcome: "Higher-quality follow-up and better funnel visibility.", talkingPoint: "I improve revenue systems by reducing friction." },
  { title: "Social Media / Lead Funnel Improvements", problem: "Content and lead capture lacked repeatable structure.", system: "Built a lightweight funnel process.", tools: "Analytics, content ops, lead routing", outcome: "More visible pipeline and easier next actions.", talkingPoint: "I connect creative motion to measurable outcomes." }
];

export const reviewItems = [
  { label: "Applications sent this week", value: "12", note: "Base habit is forming." },
  { label: "Outreach sent this week", value: "8", note: "Leverage layer warming up." },
  { label: "Interviews scheduled", value: "2", note: "Prep conversion stories." },
  { label: "Follow-ups due", value: "5", note: "Clear backlog by Friday." },
  { label: "Bottleneck", value: "Résumé tailoring speed", note: "Create one reusable bullet bank." },
  { label: "Next week focus", value: "Warm intros", note: "Add relationship leverage." },
  { label: "Second-round prep", value: "3 stories", note: "Systems, ambiguity, judgment." },
  { label: "Decision framework", value: "Commute / manager / growth / comp", note: "Do not optimize for relief only." }
];
