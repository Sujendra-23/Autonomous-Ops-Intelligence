FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# NEXT_PUBLIC_* values are inlined into the browser bundle at build time. BACKEND_URL is baked into the
# /api/* rewrite, so it must be the address the frontend container uses to reach the API.
ARG NEXT_PUBLIC_AUTH_AUTHORITY
ARG NEXT_PUBLIC_AUTH_CLIENT_ID
ARG NEXT_PUBLIC_AUTH_AUDIENCE
ARG BACKEND_URL=http://backend:8000
ENV NEXT_PUBLIC_AUTH_AUTHORITY=$NEXT_PUBLIC_AUTH_AUTHORITY NEXT_PUBLIC_AUTH_CLIENT_ID=$NEXT_PUBLIC_AUTH_CLIENT_ID NEXT_PUBLIC_AUTH_AUDIENCE=$NEXT_PUBLIC_AUTH_AUDIENCE BACKEND_URL=$BACKEND_URL
RUN npm run build
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=8080 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 8080
CMD ["node", "server.js"]
