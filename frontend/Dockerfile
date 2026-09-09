FROM node:18-alpine

WORKDIR /app

# Install dependencies from the lockfile so the image matches what CI built
COPY package.json package-lock.json ./
RUN npm ci

# Copy application code
COPY . .

# Next.js inlines NEXT_PUBLIC_* at build time, so this has to be set before
# `npm run build` - a runtime environment variable in compose arrives too late
# to affect the bundle.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

# Build the application
RUN npm run build

# Expose port
EXPOSE 3000

# Start the application
CMD ["npm", "start"]
