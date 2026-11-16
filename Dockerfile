# docker-allow: root container user not yet audited for file ownership and ports; add USER after testing
FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proton_email_server.py .
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=3004
EXPOSE 3004
CMD ["python", "proton_email_server.py"]
