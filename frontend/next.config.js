/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Trace the server's real imports into .next/standalone so the Docker image
  // can ship those instead of the whole dependency tree.
  output: 'standalone',
}

module.exports = nextConfig
