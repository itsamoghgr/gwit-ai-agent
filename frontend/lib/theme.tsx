"use client";

import { createContext, useContext, useEffect, useState } from "react";

type Theme = "gw-light" | "gw-dark";

interface ThemeCtxValue {
  theme: Theme;
  isDark: boolean;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeCtxValue>({
  theme: "gw-light",
  isDark: false,
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>("gw-light");

  useEffect(() => {
    const stored = (localStorage.getItem("gw-theme") as Theme) ?? "gw-light";
    setTheme(stored);
    document.documentElement.setAttribute("data-theme", stored);
  }, []);

  function toggle() {
    const next: Theme = theme === "gw-light" ? "gw-dark" : "gw-light";
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
