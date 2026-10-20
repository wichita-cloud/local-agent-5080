# The agent's sandbox: the container its shell commands and file edits run in when the "Protected" security
# profile is on (bin/security.sh builds it as local-agent-sandbox:1). Small on purpose: common tools for everyday
# tasks (scripts, text processing, git, downloads when you allow the sandbox network), no compilers, no cloud CLIs.
# Runs as an ordinary user, not root, so a mistake inside it can't change the container itself.
FROM debian:bookworm-slim
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq less python3 python3-venv ripgrep unzip zip \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 --shell /bin/bash agent
USER agent
WORKDIR /home/agent
CMD ["sleep", "infinity"]
