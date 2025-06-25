import React, { useState, useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const hittersData = [
  {
    name: "Pete Alonso",
    team: "Mets",
    position: "1B",
    hrScore: 94,
    hitScore: 82,
    runScore: 79,
    notes: "Barrels 13% of balls. Wind out at Citi Field. Facing flyball-prone RHP."
  },
  {
    name: "Yordan Alvarez",
    team: "Astros",
    position: "DH",
    hrScore: 92,
    hitScore: 85,
    runScore: 80,
    notes: "15.3% Barrel rate vs sliders. Minute Maid Park HR factor 108."
  },
  {
    name: "Tyler Soderstrom",
    team: "Athletics",
    position: "C",
    hrScore: 89,
    hitScore: 77,
    runScore: 70,
    notes: "18.4% Barrel rate. Facing RHP with 1.6 HR/9 allowed."
  },
  {
    name: "Kyle Tucker",
    team: "Cubs",
    position: "RF",
    hrScore: 87,
    hitScore: 90,
    runScore: 88,
    notes: "Wind out at Wrigley. xSLG .540 vs today's pitcher."
  },
  {
    name: "Corbin Carroll",
    team: "Diamondbacks",
    position: "CF",
    hrScore: 85,
    hitScore: 83,
    runScore: 90,
    notes: "HR in 2 of last 3 games. High OBP vs sinkers."
  },
  {
    name: "Shohei Ohtani",
    team: "Dodgers",
    position: "DH",
    hrScore: 83,
    hitScore: 84,
    runScore: 87,
    notes: "Facing contact-prone SP. Warm weather at Dodger Stadium."
  }
];

export default function VibeCodeApp() {
  const [category, setCategory] = useState("hrScore");

  const sortedHitters = [...hittersData].sort((a, b) => b[category] - a[category]);

  return (
    <div className="p-4 space-y-6">
      <h1 className="text-2xl font-bold text-center">VibeCode MLB Hitter Radar</h1>
      <div className="flex justify-center space-x-2">
        <Button onClick={() => setCategory("hrScore")} variant={category === "hrScore" ? "default" : "outline"}>HR</Button>
        <Button onClick={() => setCategory("hitScore")} variant={category === "hitScore" ? "default" : "outline"}>Hit</Button>
        <Button onClick={() => setCategory("runScore")} variant={category === "runScore" ? "default" : "outline"}>Run</Button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {sortedHitters.map((hitter, idx) => (
          <Card key={idx} className="rounded-2xl shadow-md">
            <CardContent className="p-4">
              <h2 className="text-xl font-semibold">{hitter.name} – {hitter.team}</h2>
              <p className="text-sm text-gray-600">Position: {hitter.position}</p>
              <p className="text-sm text-gray-800 font-medium">{category.toUpperCase()} Score: {hitter[category]}</p>
              <p className="text-sm mt-2 text-gray-700 italic">{hitter.notes}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
