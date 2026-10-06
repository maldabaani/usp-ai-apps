FROM node:20-alpine AS build
WORKDIR /app
COPY usp-ai-ba/frontend/storyforge-ui/package*.json ./
RUN npm ci
COPY usp-ai-ba/frontend/storyforge-ui/ .
RUN npx ng build --configuration production

FROM nginx:1.27-alpine
COPY --from=build /app/dist/storyforge-ui/browser /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget --no-check-certificate -qO- https://localhost/ >/dev/null || exit 1
