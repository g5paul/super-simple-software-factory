# Network allowlist checks, run inside agentsh wrap with policy sssf-net.
c() { printf '%-46s ' "$1"; shift; out=$(curl -sS -m 8 -o /dev/null -w '%{http_code}' "$@" 2>&1); echo "$out" | tail -c 90 | tr '\n' ' '; echo; }
c "allow: api.anthropic.com" https://api.anthropic.com/
c "allow: registry.npmjs.org" https://registry.npmjs.org/
c "allow: pypi.org" https://pypi.org/simple/
c "deny: example.com" https://example.com/
c "deny: github.com" https://github.com/
c "deny: raw IP, no DNS" -k https://172.66.147.243/
c "deny?: allowed IP, SNI example.com" -k --resolve example.com:443:160.79.104.10 https://example.com/
c "deny?: SNI api.anthropic.com, other IP" -k --resolve api.anthropic.com:443:172.66.147.243 https://api.anthropic.com/
c "deny: port 80 to an allowed host" http://api.anthropic.com/
printf '%-46s ' "deny: DNS for example.com"; getent hosts example.com || echo "no answer rc=$?"
printf '%-46s ' "deny: python to 1.1.1.1:443"; python3 -c 'import socket; s=socket.create_connection(("1.1.1.1",443),timeout=5); print("connected")' 2>&1 | tail -1
printf '%-46s ' "deny: localhost ssh, 127.0.0.1:22"; python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",22),timeout=5); print("connected")' 2>&1 | tail -1
printf '%-46s ' "info: proxy env"; echo "${HTTPS_PROXY:-unset}"
c "deny: direct, proxy env unset" --noproxy '*' https://api.anthropic.com/
c "deny: proxy CONNECT to agentsh API" -x "$HTTPS_PROXY" --noproxy '' -p http://127.0.0.1:18080/health
c "deny: proxy CONNECT to sshd" -x "$HTTPS_PROXY" --noproxy '' -p http://127.0.0.1:22/
c "deny: direct to 127.0.0.2:22" --noproxy '*' http://127.0.0.2:22/
# Domain fronting: CONNECT to an allowed host, TLS SNI for another site.
p=${HTTPS_PROXY#http://}
f() { printf '%-46s ' "$1"; printf 'GET / HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n' "$3" | timeout 15 openssl s_client -quiet -proxy "$p" -connect "$2:443" -servername "$3" 2>&1 | tr -d '\r' | grep -m1 -E '^HTTP/|<title>|error' | cut -c1-80; }
for h in chatgpt.com api.openai.com registry.npmjs.org api.anthropic.com pypi.org; do f "deny: front via $h, SNI example.com" "$h" example.com; done
