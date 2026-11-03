/** @type {import('tailwindcss').Config} */
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));

// The palette lives in the inline theme engine
// (templates/partials/theme_head.html) so the first paint is themed and a
// theme switch stays instant. Tailwind is compiled ahead of time, so the colour
// utilities it emits must resolve through CSS variables instead of baking in one
// palette: `--surface-*` (the warm / OLED / navy ramp) and `--accent-*` (the
// chosen accent), both set as "R G B" triplets by theme_head.html.
const SURFACE_STEPS = [
  '50', '100', '200', '300', '400', '500',
  '600', '700', '750', '800', '850', '900', '950',
];
const ACCENT_STEPS = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];

const surface = (step) => `rgb(var(--surface-${step}) / <alpha-value>)`;
const accent = (step) => `rgb(var(--accent-${step}) / <alpha-value>)`;

const ramp = (steps, value) =>
  Object.fromEntries(steps.map((step) => [step, value(step)]));

// The fixed amber ramp (alerts / warnings) is intentionally not themed.
const AMBER = {
  50: '#fff7ed', 100: '#ffedd5', 200: '#fed7aa', 300: '#fdba74',
  400: '#fb923c', 500: '#f97316', 600: '#ea580c', 700: '#c2410c',
  800: '#9a3412', 900: '#7c2d12', 950: '#431407',
};

export default {
  content: [
    `${root}/templates/**/*.html`,
    `${root}/frontend/src/**/*.js`,
  ],
  darkMode: 'class',
  theme: {
    extend: {
      screens: { xs: '480px' },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: {
        md: '0.3rem',
        lg: '0.375rem',
        xl: '0.5rem',
        '2xl': '0.625rem',
        '3xl': '0.875rem',
      },
      colors: {
        // `brand`, `emerald` and `teal` are one accent namespace under three
        // names, exactly as the old runtime config had them.
        brand: ramp(ACCENT_STEPS, accent),
        emerald: ramp(ACCENT_STEPS, accent),
        teal: ramp(ACCENT_STEPS, accent),
        slate: ramp(SURFACE_STEPS, surface),
        amber: AMBER,
      },
    },
  },
};
