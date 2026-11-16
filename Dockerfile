# docker-allow: root container user not yet audited for file ownership and ports; add USER after testing
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proton_email_server.py .
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=3004
EXPOSE 3004
CMD ["python", "proton_email_server.py"]
