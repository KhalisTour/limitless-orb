import { useEffect, useState } from "react";

const STORAGE_KEY = "operation-exit-rep-state";

export function useRepCompletion() {
  const [state, setState] = useState({ completed: false, completedRep: null, xpClaimed: 0 });

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved) setState(JSON.parse(saved));
  }, []);

  function completeRep(rep) {
    const nextState = { completed: true, completedRep: rep.title, xpClaimed: rep.xp };
    setState(nextState);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(nextState));
  }

  function resetRep() {
    const nextState = { completed: false, completedRep: null, xpClaimed: 0 };
    setState(nextState);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(nextState));
  }

  return { ...state, completeRep, resetRep };
}
