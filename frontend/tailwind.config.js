/** @type {import('tailwindcss').Config} */
// Бренд-токены Ponimaiu (источник — ponimaiu_docs/app/global.css): коралловый акцент
// на холодновато-нейтральном «медицинском» фоне, голубой azure как контр-акцент.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        coral: {
          DEFAULT: "#F4725E", // знак бренда (литеральный hex брендбука)
          mark: "#F37662", // рантайм-коралл знака
          500: "#E75740", // primary: ссылки/акцент (контраст на белом)
          600: "#E64F37", // hover/active/strong
          soft: "#FDEFED", // мягкая подложка
        },
        ink: {
          DEFAULT: "#202732", // основной текст/заголовки
          muted: "#606E80", // подписи/вторичный
        },
        canvas: "#FCFCFD", // «медицинский» фон (едва голубоватый)
        line: "#DEE3E8", // границы/разделители
        azure: {
          DEFAULT: "#759EC7", // холодный контр-акцент: фокус, данные, «tech»
          deep: "#3E7CB1",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      borderRadius: {
        card: "0.875rem",
        control: "0.5rem",
        chip: "0.375rem",
      },
      boxShadow: {
        // мягкие «медицинские» тени с синим подтоном
        card: "0 1px 2px rgba(15,25,50,.06), 0 24px 48px -16px rgba(15,25,50,.12)",
        soft: "0 1px 2px rgba(15,25,50,.05), 0 8px 24px -12px rgba(15,25,50,.10)",
        lift: "0 2px 4px rgba(15,25,50,.06), 0 16px 32px -12px rgba(15,25,50,.16)",
      },
      letterSpacing: {
        tightest: "-0.02em",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-node": {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: {
        "fade-up": "fade-up 320ms cubic-bezier(0.16,1,0.3,1) both",
        "pulse-node": "pulse-node 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
