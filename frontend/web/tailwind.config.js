/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#090d16",
        surface: "#111827",
        surfaceElevated: "#1e293b",
        border: "#1e293b",
        accent: "#38bdf8",
      },
    },
  },
  plugins: [],
};
