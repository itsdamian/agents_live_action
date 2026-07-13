# Static vanilla-JS frontend served by Nginx.
# Phase 0: no node/Vite build — the Vue 3 rewrite is deferred to Phase 2.
FROM nginx:1.27-alpine

# Reverse-proxy config: serve static files + proxy /api and /ws to backend.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static assets (index.html, css/, js/) served directly by Nginx.
COPY index.html /usr/share/nginx/html/index.html
COPY css/ /usr/share/nginx/html/css/
COPY js/ /usr/share/nginx/html/js/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
