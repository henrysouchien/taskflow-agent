.PHONY: serve dev stop status

PYTHON := venv/bin/python

# Start server in foreground (Ctrl-C to stop)
serve:
	$(PYTHON) -m src.web

# Start with auto-reload for development
dev:
	$(PYTHON) -m uvicorn src.web:app --host 127.0.0.1 --port 8787 --reload

# Stop whatever is listening on port 8787 (works for both foreground and MCP-managed)
# Waits for exit before cleaning up PID file to avoid orphaning a managed process.
stop:
	@PID=$$(lsof -nP -i :8787 -sTCP:LISTEN -t 2>/dev/null); \
	if [ -n "$$PID" ]; then \
		kill $$PID 2>/dev/null || true; \
		echo "Sent SIGTERM to PID $$PID, waiting..."; \
		for i in 1 2 3 4 5 6 7 8 9 10; do \
			kill -0 $$PID 2>/dev/null || break; \
			sleep 0.5; \
		done; \
		if kill -0 $$PID 2>/dev/null; then \
			echo "Still alive, sending SIGKILL"; \
			kill -9 $$PID 2>/dev/null || true; \
			sleep 1; \
		fi; \
		if ! kill -0 $$PID 2>/dev/null; then \
			rm -f data/taskflow-web.pid; \
			echo "Stopped"; \
		else \
			echo "Failed to stop PID $$PID — PID file retained"; \
		fi; \
	else \
		echo "Not running"; \
	fi

# Check if server is listening
status:
	@PID=$$(lsof -nP -i :8787 -sTCP:LISTEN -t 2>/dev/null); \
	if [ -n "$$PID" ]; then \
		echo "Running (PID $$PID)"; \
	else \
		echo "Not running"; \
	fi
