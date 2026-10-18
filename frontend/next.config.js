const { PHASE_DEVELOPMENT_SERVER } = require('next/constants')

/**
 * Three ways this app is built, one config:
 *
 * - Vercel: a normal Next build. NEXT_PUBLIC_API_BASE_URL points at the
 *   backend project, which is a different origin.
 * - The container (Dockerfile at the repo root): NEXT_OUTPUT=export writes a
 *   static site to out/, which FastAPI serves at / beside /api. Same origin, so
 *   the API base URL is empty. The page is all client-side, so nothing is lost.
 * - `npm run dev`: /api is proxied to the backend on :8000, so the browser
 *   sees one origin here too and no CORS setup is needed.
 *
 * @type {(phase: string) => import('next').NextConfig}
 */
module.exports = (phase) => {
  const config = {
    reactStrictMode: true,
    output: process.env.NEXT_OUTPUT === 'export' ? 'export' : undefined,
  }

  if (phase === PHASE_DEVELOPMENT_SERVER) {
    const backend = process.env.BACKEND_URL || 'http://localhost:8000'
    config.rewrites = async () => [{ source: '/api/:path*', destination: `${backend}/api/:path*` }]
  }

  return config
}
