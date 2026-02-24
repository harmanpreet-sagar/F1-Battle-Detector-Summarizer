import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'F1 Battle Detector',
  description: 'Real-time F1 battle detection and tracking',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="bg-f1-dark text-white min-h-screen">
        {children}
      </body>
    </html>
  )
}
