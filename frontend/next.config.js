/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Trace the server's real imports into .next/standalone so the Docker image
  // can ship those instead of the whole dependency tree. Vercel builds its own
  // bundle and warns when it finds this set, so leave it off there.
  output: process.env.VERCEL ? undefined : 'standalone',
}

module.exports = nextConfig
