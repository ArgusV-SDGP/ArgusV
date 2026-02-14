# Service Specification: Admin Dashboard (`dashboard`)

> **Status**: DRAFT  
> **Type**: Frontend Application (Containerized)  
> **Language**: TypeScript (React)  
> **Responsibility**: Hosting the user interface and proxying API requests.

---

## 1. Business Logic & Responsibility
This specification covers the **deployment artifact** for the UI. The internal architecture of the UI is detailed in `ArgusV_UI_Implementation_Plan.md`. This service is responsible for delivering that application to the user's browser securely.

### Core Capabilities:
1.  **Static Serving**: Delivering the compiled React/Vite assets (`index.html`, `assets/*.js`).
2.  **API Proxying (Dev/Prod)**: Routing `/api/*` requests to the backend microservices (so the browser doesn't deal with CORS).
3.  **WebSocket Upgrading**: Handling the connection upgrade for `/ws/alerts`.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: HTTP Requests from Browser.
-   **Output**: HTML/JS/CSS assets.

### 2.2 Technical Stack
-   **Build Tool**: `Vite` (npm run build).
-   **Production Server**: `Nginx` (Alpine Linux).
-   **Container**: Docker Multi-stage build.

### 2.3 Nginx Configuration Strategy
The container handles routing to backend services:
```nginx
server {
    listen 80;
    
    # Serve React App
    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;
    }

    # Proxy API to Microservices
    location /api/incidents {
        proxy_pass http://decision-engine-service:8000;
    }
    
    # Proxy WebSocket
    location /ws {
        proxy_pass http://decision-engine-service:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
    }
}
```

---

## 3. MVP Implementation Steps
1.  **Dockerization**: Write a `Dockerfile` that:
    -   Stage 1 (Node): `npm install && npm run build`.
    -   Stage 2 (Nginx): Copy `dist/` to `/usr/share/nginx/html`.
2.  **Nginx Config**: Map the `/api` routes to the internal Docker DNS names of other services.
3.  **Environment Integration**: Ensure `VITE_API_URL` is handled correctly (usually relative path `/api` so Nginx handles it).
