.PHONY: dev backend frontend install build clean

# Start both backend and frontend for development
dev: backend frontend

# Start the FastAPI backend on port 8420
backend:
	.venv/bin/python -m ephemeralos &

# Start the Vite dev server on port 5173 (proxies /api to backend)
frontend:
	cd frontend/web && npm run dev

# Install all dependencies
install:
	uv sync
	cd frontend/web && npm install

# Build the frontend for production
build:
	cd frontend/web && npm run build

# Start production server (serves built frontend from dist/)
serve:
	.venv/bin/python -m ephemeralos

# Clean build artifacts
clean:
	rm -rf frontend/web/dist frontend/web/node_modules/.vite
