/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {brand: {navy:'#142742',navyDark:'#0D1B2A',navyLight:'#1E3A5F',accent:'#8C6D3E',borderLight:'#E8E2D5',textDark:'#1C2430',textMuted:'#6B7280'}},
      fontFamily: {
        serifBrand: ['Cinzel', 'serif'],
        // Georgia (Tailwind's default serif on Windows) lacks stacked Vietnamese
        // diacritics; these offline system fonts render them correctly.
        serif: ['"Noto Serif"', 'Cambria', '"Times New Roman"', 'serif'],
        sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
