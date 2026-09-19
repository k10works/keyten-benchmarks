"""Exercise benchmark daemon failure and ownership behavior without datasets."""
import os
import http.server
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HELPER = Path(__file__).parent / 'lib/daemon.sh'


class DaemonTests(unittest.TestCase):
    def run_shell(self, command, **overrides):
        env = {k: v for k, v in os.environ.items()
               if k not in {'BENCH_PORT', 'BENCH_POLARS_PORT', 'BENCH_STARTUP_TIMEOUT'}}
        env.update(overrides)
        return subprocess.run(['bash', '-c', 'set -euo pipefail; source "$1"; '
                               'bench_daemon_configure; trap bench_stop_daemon EXIT; ' + command,
                               'daemon-test', str(HELPER)], env=env, text=True,
                              capture_output=True, timeout=15)

    def test_one_port_for_both_adapters_and_legacy_alias(self):
        for settings, expected in [({}, '8000'), ({'BENCH_PORT': '18768'}, '18768'),
                                   ({'BENCH_POLARS_PORT': '18769'}, '18769'),
                                   ({'BENCH_PORT': '01870'}, '1870')]:
            with self.subTest(settings=settings):
                result = self.run_shell('echo "$BENCH_PORT $BENCH_POLARS_PORT"', **settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f'{expected} {expected}')

    def test_invalid_or_conflicting_configuration_fails_before_work(self):
        for settings in [{'BENCH_PORT': '0'}, {'BENCH_PORT': '65536'},
                         {'BENCH_PORT': 'garbage'}, {'BENCH_STARTUP_TIMEOUT': '0'},
                         {'BENCH_STARTUP_TIMEOUT': '99999'},
                         {'BENCH_PORT': '18768', 'BENCH_POLARS_PORT': '18769'}]:
            with self.subTest(settings=settings):
                result = self.run_shell('echo started', **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('started', result.stdout)

    def test_health_probe_uses_configured_port_and_rejects_http_failure(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            status = 200

            def do_GET(self):
                self.send_response(self.status if self.path == '/health' else 404)
                self.end_headers()

            def log_message(self, *_):
                pass

        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                port = str(server.server_address[1])
                result = self.run_shell('bench_daemon_healthy', BENCH_PORT=port)
                self.assertEqual(result.returncode, 0, result.stderr)
                Handler.status = 503
                result = self.run_shell('bench_daemon_healthy', BENCH_PORT=port)
                self.assertNotEqual(result.returncode, 0)
            finally:
                server.shutdown()
                worker.join()

    def test_dead_child_fails_immediately_with_exit_status(self):
        started = time.monotonic()
        result = self.run_shell("bash -c 'exit 23' & BENCH_DAEMON_PID=$!; "
                                'sleep 0.1; bench_wait_daemon "$BENCH_DAEMON_PID"',
                                BENCH_STARTUP_TIMEOUT='10')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('status=23', result.stderr)
        self.assertLess(time.monotonic() - started, 5)

    def test_unhealthy_live_child_times_out_and_is_reaped(self):
        result = self.run_shell('bench_daemon_healthy() { return 1; }; '
                                'sleep 30 & BENCH_DAEMON_PID=$!; echo "$BENCH_DAEMON_PID"; '
                                'bench_wait_daemon "$BENCH_DAEMON_PID"',
                                BENCH_STARTUP_TIMEOUT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('startup timed out', result.stderr)
        pid = int(result.stdout.strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_ready_child_is_owned_and_cleaned_on_client_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_shell(
                '''bench_daemon_healthy() { [ -f "$READY_FILE" ]; }
                "$TEST_PYTHON" -c 'import os,time; open(os.environ["READY_FILE"],"w").close(); time.sleep(30)' &
                BENCH_DAEMON_PID=$!; echo "$BENCH_DAEMON_PID"
                bench_wait_daemon "$BENCH_DAEMON_PID"; exit 7
                ''', TEST_PYTHON=sys.executable, READY_FILE=str(Path(tmp) / 'ready'))
        self.assertEqual(result.returncode, 7)
        self.assertNotIn('did not stop', result.stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(result.stdout.strip()), 0)

    def test_existing_service_is_rejected_without_being_killed(self):
        with subprocess.Popen(['sleep', '30']) as unrelated:
            try:
                result = self.run_shell('bench_daemon_healthy() { return 0; }; '
                                        'bench_daemon_available; echo incorrectly-accepted')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('existing service', result.stderr)
                self.assertNotIn('incorrectly-accepted', result.stdout)
                self.assertIsNone(unrelated.poll())
            finally:
                unrelated.terminate()
                unrelated.wait()

    def test_child_ignoring_termination_is_killed_after_bounded_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_shell(
                '''"$TEST_PYTHON" -c 'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open(os.environ["READY_FILE"],"w").close(); time.sleep(30)' &
                BENCH_DAEMON_PID=$!; echo "$BENCH_DAEMON_PID"
                until [ -f "$READY_FILE" ]; do sleep 0.05; done
                bench_stop_daemon
                ''', TEST_PYTHON=sys.executable, READY_FILE=str(Path(tmp) / 'ready'))
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(result.stdout.strip()), 0)


if __name__ == '__main__':
    unittest.main()
