"use client";

import { createContext, useContext, useEffect, useState } from "react";

type Theme = "gw-dark" | "gw-dark";

interface ThemeCtxValue {
  theme: Theme;
  isDark: boolean;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeCtxValue>({
  theme: "gw-dark",
  isDark: false,
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>("gw-dark");

  useEffect(() => {
    const stored = (localStorage.getItem("gw-theme") as Theme) ?? "gw-dark";
    setTheme(stored);
    document.documentElement.setAttribute("data-theme", stored);
  }, []);

  function toggle() {
    const next: Theme = theme === "gw-dark" ? "gw-dark" : "gw-dark";
    setTheme(next);
    localStorage.setItem("gw-theme", next);
    document.documentElement.setAttribute("data-theme", next);
  }

  return (
    <ThemeContext.Provider value={{ theme, isDark: theme === "gw-dark", toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
