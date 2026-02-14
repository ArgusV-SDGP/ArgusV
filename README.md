# ArgusV Local Development

This project uses Docker Compose to orchestrate 7 microservices and the necessary infrastructure (Kafka, Postgres, Redis, MinIO, Qdrant).

## Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for local script execution if needed)

## Setup

1. **Environment Variables**
   Copy `.env.example` to `.env` and fill in your API keys (OpenAI is required for VLM/RAG).
   ```bash
   cp .env.example .env
   ```

2. **Start Infrastructure (Dev Mode)**
   Use the `.dev` file to enable hot-reloading (changes to code apply instantly).
   ```bash
   docker-compose -f docker-compose.dev.yml up -d
   ```

3. **Start Production**
   Use the `.prod` file for stable, immutable containers.
   ```bash
   docker-compose -f docker-compose.prod.yml up -d --build
   ```

4. **Access Endpoints**

   - **Dashboard**: http://localhost:3000
   - **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)
   - **RAG Chat API**: http://localhost:8007/docs

## Directory Structure
- `services/`: Source code for all microservices.
- `configs/`: Configuration files for infrastructure (Mosquitto, Prometheus).
- `docs/`: Architecture and Specification documentation.
