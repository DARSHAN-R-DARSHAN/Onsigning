import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "#F5F9FF",
        border: "#DCE6F1",
        navy: "#172B4D",
        "text-secondary": "#64748B",
        primary: {
          DEFAULT: "#2563EB",
          hover: "#1D4ED8",
        },
        blue: {
          50: "#F0F7FF",
          100: "#EAF2FF",
          200: "#DCEBFF",
          300: "#93C5FD",
          500: "#3B82F6",
          600: "#2563EB",
          700: "#1D4ED8",
        },
        severity: {
          critical: {
            text: "#B42318",
            surface: "#FEF3F2",
            border: "#FECDCA",
          },
          high: {
            text: "#C2410C",
            surface: "#FFF7ED",
            border: "#FFEDD5",
          },
          medium: {
            text: "#92400E",
            surface: "#FEFCE8",
            border: "#FEF08A",
          },
          low: {
            text: "#1D4ED8",
            surface: "#EFF6FF",
            border: "#DBEAFE",
          },
          compliant: {
            text: "#166534",
            surface: "#F0FDF4",
            border: "#DCFCE7",
          },
          undecided: {
            text: "#475569",
            surface: "#F8FAFC",
            border: "#E2E8F0",
          },
        },
      },
      borderRadius: {
        sm: "0.375rem",
        md: "0.5rem",
        lg: "0.75rem",
        xl: "1rem",
      },
      boxShadow: {
        card: "0 1px 2px rgb(23 43 77 / 5%)",
        popover: "0 8px 24px rgb(23 43 77 / 12%)",
      },
    },
  },
  plugins: [],
};

export default config;
