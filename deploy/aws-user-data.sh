#!/usr/bin/env bash
# Bootstrap da instância RemuneData (Ubuntu 24.04, t3.micro): swap, Docker e Nginx.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# 1 GB de RAM: swap de 2 GB é obrigatório para Postgres + API + worker
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=20' > /etc/sysctl.d/99-swap.conf && sysctl -p /etc/sysctl.d/99-swap.conf

apt-get update
apt-get install -y ca-certificates curl nginx docker.io docker-compose-v2 ec2-instance-connect
systemctl enable --now docker
usermod -aG docker ubuntu
mkdir -p /opt/remunedata /var/www/remunedata
chown -R ubuntu:ubuntu /opt/remunedata
touch /var/lib/cloud/instance/remunedata-ready
