#!/bin/bash
set -e

# Função para esperar um serviço estar pronto
wait_for_service() {
    local host=$1
    local port=$2
    local name=$3
    local timeout=${4:-60}
    
    echo "Waiting for $name ($host:$port) with ${timeout}s timeout..."
    python3 -c "
import socket
import time
import os

host = '$host'
port = $port
timeout = $timeout
start_time = time.time()
while time.time() - start_time < timeout:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f'Service $name is up!')
            exit(0)
    except (socket.gaierror, socket.error):
        time.sleep(1)
        print('.', end='', flush=True)
exit(1)
"
}

# Esperar DB e Redis se as variáveis estiverem definidas
if [ -n "$DB_HOST" ]; then
    wait_for_service "$DB_HOST" 5432 "Database"
fi

if [ -n "$REDIS_HOST" ]; then
    wait_for_service "$REDIS_HOST" 6379 "Redis"
fi

echo "Starting application..."
exec "$@"
