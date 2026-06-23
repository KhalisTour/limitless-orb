import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/Primitives";
import { AskCoachDrawer } from "./components/CareerCommand";
import Dashboard from "./pages/Dashboard";
import Home from "./pages/Home";
import InterviewGym from "./pages/InterviewGym";
import Outreach from "./pages/Outreach";
import Portfolio from "./pages/Portfolio";
import Review from "./pages/Review";
import Roadmap from "./pages/Roadmap";
import TodaysRep from "./pages/TodaysRep";
import Tracker from "./pages/Tracker";
import ResumeLab from "./pages/ResumeLab";

export default function App() {
  return (
    <AppShell headerAction={<AskCoachDrawer />}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/roadmap" element={<Roadmap />} />
        <Route path="/todays-rep" element={<TodaysRep />} />
        <Route path="/tracker" element={<Tracker />} />
        <Route path="/resume-lab" element={<ResumeLab />} />
        <Route path="/outreach" element={<Outreach />} />
        <Route path="/interview-gym" element={<InterviewGym />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/review" element={<Review />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
