#!/usr/bin/env bash
# Local benchmark daemon lifecycle. Own only the child started by this runner.
bench_daemon_configure() {
  if [ -n "${BENCH_PORT:-}" ] && [ -n "${BENCH_POLARS_PORT:-}" ] && [ "$BENCH_PORT" != "$BENCH_POLARS_PORT" ]; then
    echo 'ClickBench: BENCH_PORT and BENCH_POLARS_PORT must agree' >&2
    return 1
  fi
  local port="${BENCH_PORT:-${BENCH_POLARS_PORT:-8000}}"
  local timeout="${BENCH_STARTUP_TIMEOUT:-180}"
  if ! [[ "$port" =~ ^[0-9]{1,5}$ ]] || ((10#$port < 1 || 10#$port > 65535)); then
    echo 'ClickBench: port must be between 1 and 65535' >&2
    return 1
  fi
  if ! [[ "$timeout" =~ ^[0-9]{1,4}$ ]] || ((10#$timeout < 1 || 10#$timeout > 3600)); then
    echo 'ClickBench: BENCH_STARTUP_TIMEOUT must be between 1 and 3600 seconds' >&2
    return 1
  fi
  export BENCH_PORT="$((10#$port))" BENCH_POLARS_PORT="$((10#$port))"
  BENCH_STARTUP_TIMEOUT="$((10#$timeout))"
  BENCH_DAEMON_PID=''
}

bench_daemon_healthy() {
  curl --connect-timeout 1 --max-time 1 -sf "http://127.0.0.1:$BENCH_PORT/health" >/dev/null 2>&1
}

bench_daemon_available() {
  if bench_daemon_healthy; then
    echo "ClickBench: an existing service answers /health on port $BENCH_PORT; refusing to use it" >&2
    return 1
  fi
}

bench_wait_daemon() {
  local pid="$1" deadline=$((SECONDS + BENCH_STARTUP_TIMEOUT)) status
  while true; do
    if ! kill -0 "$pid" 2>/dev/null; then
      status=0
      wait "$pid" || status=$?
      echo "ClickBench: daemon exited before readiness (pid=$pid, status=$status)" >&2
      return 1
    fi
    if bench_daemon_healthy && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    if ((SECONDS >= deadline)); then
      echo "ClickBench: daemon startup timed out after ${BENCH_STARTUP_TIMEOUT}s (pid=$pid, port=$BENCH_PORT)" >&2
      return 1
    fi
    sleep 1
  done
}

bench_stop_daemon() {
  local pid="${BENCH_DAEMON_PID:-}" deadline=$((SECONDS + 10)) state=''
  [ -n "$pid" ] || return 0
  kill "$pid" 2>/dev/null || true
  while kill -0 "$pid" 2>/dev/null && ((SECONDS < deadline)); do
    # During an EXIT trap Bash can defer reaping. A zombie is already stopped;
    # kill -0 alone would wait out the entire grace period before our wait.
    state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
    [[ "$state" == Z* ]] && break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null && [[ "$state" != Z* ]]; then
    echo "ClickBench: daemon did not stop within 10s; killing owned child pid=$pid" >&2
    kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
  BENCH_DAEMON_PID=''
}
