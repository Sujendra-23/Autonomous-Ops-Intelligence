FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_AUTH_AUTHORITY
ARG VITE_AUTH_CLIENT_ID
ARG VITE_AUTH_AUDIENCE
ENV VITE_AUTH_AUTHORITY=$VITE_AUTH_AUTHORITY VITE_AUTH_CLIENT_ID=$VITE_AUTH_CLIENT_ID VITE_AUTH_AUDIENCE=$VITE_AUTH_AUDIENCE
RUN npm run build
FROM nginxinc/nginx-unprivileged:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
