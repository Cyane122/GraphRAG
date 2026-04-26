import { useEffect } from "react";

const TIME_GRADIENTS = {
  dawn:    { from: "#090920", mid: "#1e1045", to: "#4a2060" },
  morning: { from: "#120830", mid: "#6b2d72", to: "#c45c87" },
  day:     { from: "#0a1e4a", mid: "#1a3a8a", to: "#2563c8" },
  evening: { from: "#150830", mid: "#8b2d5a", to: "#d4603a" },
  night:   { from: "#04040f", mid: "#08081e", to: "#0d0d2e" },
};

function hourToPeriod(hour) {
  const h = Number(hour);
  if (h >= 5  && h < 9)  return "dawn";
  if (h >= 9  && h < 17) return "day";
  if (h >= 17 && h < 21) return "evening";
  if (h >= 21 || h < 5)  return "night";
  return "night";
}

export default function TimeTheme({ props }) {
  const { hour } = props || {};

  useEffect(() => {
    if (hour == null) return;

    const period = hourToPeriod(hour);
    const g      = TIME_GRADIENTS[period];
    const root   = document.documentElement;

    root.style.setProperty("--time-bg-from", g.from);
    root.style.setProperty("--time-bg-mid",  g.mid);
    root.style.setProperty("--time-bg-to",   g.to);
    root.setAttribute("data-time", period);
  }, [hour]);

  return null;
}