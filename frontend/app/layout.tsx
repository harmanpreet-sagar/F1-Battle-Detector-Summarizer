import type { Metadata } from 'next'
import { Barlow, Barlow_Condensed, JetBrains_Mono } from 'next/font/google'
import './globals.css'

// Condensed caps for names and labels, a plain sans for prose, a mono for
// every number so gaps and lap counts line up as they tick.
const display = Barlow_Condensed({
  subsets: ['latin'],
  weight: ['600', '700', '800'],
  variable: '--font-display',
})
const sans = Barlow({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-sans',
})
const mono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['500', '700'],
  variable: '--font-mono',
})

export const metadata: Metadata = {
  title: 'F1 Battle Detector',
  description: 'Every on-track battle, detected and explained, replayed from real race data',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-ink-950 font-sans text-white antialiased">
        {children}
      </body>
    </html>
  )
}
