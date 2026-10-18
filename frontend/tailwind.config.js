/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'f1-red': '#E10600',
        'f1-dark': '#15151E',
        // Direction A: the timing tower. Darkest at the page, lifting toward cards.
        ink: {
          950: '#0B0B10', // page
          900: '#101017', // header band
          850: '#111118', // tower
          800: '#15151E', // card
          750: '#17171F', // highlighted tower row
          700: '#1B1B24', // tower row rule
          600: '#23232D', // panel border, track
          500: '#2E2E3A', // card border
        },
        mute: {
          DEFAULT: '#9C9CAB', // secondary text, 4.5:1+ on every ink surface
          strong: '#B4B4C2',
          soft: '#D0D0DA',
        },
        watch: '#F59E0B',
        closing: '#4ADE80',
        opening: '#F87171',
        sc: '#F5C518',
      },
      fontFamily: {
        display: ['var(--font-display)', 'sans-serif'],
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
