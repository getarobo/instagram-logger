/** @type {import('tailwindcss').Config} */
// Tailwind v4 uses CSS-first config; this file is kept as a small marker so
// editors / older tooling still find a config. The real config lives in
// `src/index.css` via `@import "tailwindcss"` and `@theme`.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
};
