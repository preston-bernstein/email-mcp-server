# docker-allow: root container user not yet audited for file ownership and ports; add USER after testing
FROM python:3.15.2-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proton_email_server.py .
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=3004
EXPOSE 3004
CMD ["python", "proton_email_server.py"]
