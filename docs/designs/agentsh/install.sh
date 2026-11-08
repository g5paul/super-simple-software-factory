#!/usr/bin/env bash
# Install agentsh and run its server as the agentsh service.
# Run it in your own terminal: sudo bash install.sh <path to agentsh_0.20.5_linux_amd64.deb>
set -euo pipefail

deb="${1:?give the path to agentsh_0.20.5_linux_amd64.deb}"
here="$(cd "$(dirname "$0")" && pwd)"
want=3967b3e5713c368f6b342ed4caee20a10cb604c53af24b9850dcecce49db98b0

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
[ "$(sha256sum "$deb" | cut -d' ' -f1)" = "$want" ] || { echo "checksum does not match"; exit 1; }

echo "1. Install the package."
dpkg -i "$deb"

echo "2. Add the agentsh system user."
getent passwd agentsh >/dev/null || useradd --system --home-dir /var/lib/agentsh --shell /usr/sbin/nologin agentsh

echo "3. Install the config and the policy, owned by root."
install -o root -g root -m 0644 "$here/sssf.yaml" /etc/agentsh/sssf.yaml
install -o root -g root -m 0644 "$here/sssf-proof.yaml" /etc/agentsh/policies/sssf-proof.yaml

echo "4. Install and start the service."
install -o root -g root -m 0644 "$here/agentsh.service" /etc/systemd/system/agentsh.service
systemctl daemon-reload
systemctl enable --now agentsh.service

echo "5. Check it."
sleep 3
systemctl is-active agentsh.service
curl -s --unix-socket /run/agentsh/agentsh.sock http://agentsh/health; echo
grep CapEff "/proc/$(systemctl show -p MainPID --value agentsh.service)/status"
echo "Done."
