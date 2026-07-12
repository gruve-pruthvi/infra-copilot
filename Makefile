.PHONY: install backend frontend dev test seed seed-clean

install:
	cd backend && uv sync
	cd frontend && npm install

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

# run both (Ctrl-C stops both)
dev:
	$(MAKE) -j2 backend frontend

test:
	cd backend && uv run pytest -q

# Test fixture environment: a healthy service, a crash-looping container (RCA
# target), an idle container + orphan volume + dangling image (cost targets).
seed:
	-docker run -d --name seed-nginx -p 8081:80 nginx:alpine
	-docker run -d --name seed-broken-postgres --restart on-failure postgres:16
	-docker run -d --name seed-idle busybox sleep infinity
	-docker volume create seed-orphan-volume
	@echo "Seeded. seed-broken-postgres will crash-loop (missing POSTGRES_PASSWORD)."

seed-clean:
	-docker rm -f seed-nginx seed-broken-postgres seed-idle
	-docker volume rm seed-orphan-volume
