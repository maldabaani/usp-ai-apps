FROM node:20-alpine AS build
WORKDIR /app
COPY usp-ai-ba/frontend/storyforge-ui/package*.json ./
RUN npm ci
COPY usp-ai-ba/frontend/storyforge-ui/ .
RUN npx ng build --configuration production

FROM nginx:1.27-alpine
COPY --from=build /app/dist/storyforge-ui/browser /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
