import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: { extend: { colors: { "bg-primary": "rgb(var(--bg-primary) / <alpha-value>)", "bg-secondary": "rgb(var(--bg-secondary) / <alpha-value>)", surface: "rgb(var(--surface) / <alpha-value>)", line: "var(--line)", "line-strong": "var(--line-strong)", "text-primary": "rgb(var(--text-primary) / <alpha-value>)", "text-secondary": "rgb(var(--text-secondary) / <alpha-value>)", "text-tertiary": "rgb(var(--text-tertiary) / <alpha-value>)", neon: "rgb(var(--neon) / <alpha-value>)", "neon-dim": "var(--neon-dim)", warning: "rgb(var(--warning) / <alpha-value>)" }, boxShadow: { card: "var(--shadow-card)", "card-hover": "var(--shadow-card-hover)" } } },
  plugins: [],
};

export default config;
