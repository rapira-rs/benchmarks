"""Tests of the server config templates, the shared php.ini, and the expected body files."""

import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG = "/home/fedora/bench-rig"

TOML_CASES = [
    {
        "name": "rapira http worker",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": ":8080", "ENTRY": RIG + "/apps/hello/worker.php", "MODE": "worker", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "pool": {"entrypoint": RIG + "/apps/hello/worker.php", "mode": "worker", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira http behind nginx",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": "127.0.0.1:8081", "ENTRY": "/opt/bench/apps/symfony/bench/worker-rapira.php", "MODE": "worker", "PROCS": "2"},
        "expected": {
            "http": {
                "listen": "127.0.0.1:8081",
                "pool": {"entrypoint": "/opt/bench/apps/symfony/bench/worker-rapira.php", "mode": "worker", "processes": 2},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira static hit and miss",
        "path": "servers/rapira/static.toml.tpl",
        "values": {"LISTEN": ":8080", "ROOT": RIG + "/apps/static", "ENTRY": RIG + "/apps/hello/worker.php", "MODE": "worker", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "middleware": ["static"],
                "static": {"root": RIG + "/apps/static"},
                "pool": {"entrypoint": RIG + "/apps/hello/worker.php", "mode": "worker", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira grpc",
        "path": "servers/rapira/grpc.toml.tpl",
        "values": {"LISTEN": ":8080", "RIG": RIG, "ENTRY": RIG + "/apps/grpc/php/dispatcher.php", "PROCS": "32"},
        "expected": {
            "grpc": {
                "listen": ":8080",
                "descriptor_set": RIG + "/apps/grpc/bench.binpb",
                "services": ["bench.v1.EchoService"],
                "pool": {"entrypoint": RIG + "/apps/grpc/php/dispatcher.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
]

TEXT_CASES = [
    {
        "name": "frankenphp worker skips the document root",
        "path": "servers/frankenphp/worker.Caddyfile.tpl",
        # num_threads is the worker num plus one.
        "values": {"LISTEN": ":8080", "THREADS": "33", "PROCS": "32", "DOCROOT": RIG + "/apps/hello", "ENTRY": RIG + "/apps/hello/frankenphp.php", "ENV": ""},
        "present": ["grace_period 2s", "admin off", "auto_https off", "num_threads 33", "num 32", "file_server off", "match *", "file " + RIG + "/apps/hello/frankenphp.php", ":8080 {"],
        "absent": ["encode", "try_files"],
    },
    {
        "name": "frankenphp worker with Octane env lines",
        "path": "servers/frankenphp/worker.Caddyfile.tpl",
        "values": {"LISTEN": ":8080", "THREADS": "3", "PROCS": "2", "DOCROOT": "/opt/bench/apps/laravel/public", "ENTRY": "/opt/bench/apps/laravel/public/frankenphp-worker.php", "ENV": "\t\t\tenv LARAVEL_OCTANE 1\n\t\t\tenv APP_DEBUG false\n"},
        "present": ["\t\t\tenv LARAVEL_OCTANE 1\n\t\t\tenv APP_DEBUG false\n", "num_threads 3", "num 2", "match *"],
        "absent": ["encode"],
    },
    {
        "name": "frankenphp classic runs the index file",
        "path": "servers/frankenphp/classic.Caddyfile.tpl",
        # num_threads is the pool size in the classic shape.
        "values": {"LISTEN": ":8080", "THREADS": "32", "DOCROOT": "/opt/bench/apps/symfony/public", "INDEX": "index.php"},
        "present": ["grace_period 2s", "num_threads 32", "file_server off", "try_files {path} index.php", "root * /opt/bench/apps/symfony/public"],
        "absent": ["worker", "encode"],
    },
    {
        "name": "frankenphp stock keeps the file server",
        "path": "servers/frankenphp/stock.Caddyfile.tpl",
        "values": {"LISTEN": ":8080", "THREADS": "33", "PROCS": "32", "DOCROOT": RIG + "/apps/static", "INDEX": "index.php", "ENV": ""},
        "present": ["grace_period 2s", "num_threads 33", "num 32", "file " + RIG + "/apps/static/index.php", "index index.php", "try_files {path} {path}/index.php index.php", "root * " + RIG + "/apps/static"],
        "absent": ["file_server off", "match *", "encode"],
    },
    {
        "name": "nginx in front of rapira",
        "path": "servers/nginx/rapira.conf.tpl",
        "values": {"PROCS": "32", "LISTEN": "8080"},
        "present": ["worker_processes 32;", "listen 8080 backlog=65535;", "server 127.0.0.1:8081;", "keepalive_requests 1000000;"],
        "absent": [],
    },
    {
        "name": "nginx in front of php-fpm",
        "path": "servers/nginx/fpm.conf.tpl",
        "values": {"PROCS": "32", "LISTEN": "8080", "DOCROOT": "/opt/bench/apps/symfony/public", "INDEX": "index.php"},
        "present": ["worker_processes 32;", "listen 8080 backlog=65535;", "root /opt/bench/apps/symfony/public;", "fastcgi_pass 127.0.0.1:9000;", "SCRIPT_FILENAME  $document_root/index.php;", "SCRIPT_NAME      /index.php;"],
        "absent": ["include"],
    },
    {
        "name": "php-fpm static pool",
        "path": "servers/php-fpm/php-fpm.conf.tpl",
        "values": {"PROCS": "32"},
        "present": ["pm = static", "pm.max_children = 32", "listen = 127.0.0.1:9000"],
        "absent": [],
    },
    {
        "name": "roadrunner grpc",
        "path": "servers/roadrunner/grpc.rr.yaml.tpl",
        "values": {"RIG": RIG, "LISTEN": "0.0.0.0:8080", "PROCS": "32"},
        "present": ['listen: "tcp://0.0.0.0:8080"', "num_workers: 32", 'command: "php ' + RIG + '/apps/grpc/php/rr-worker.php"', '["' + RIG + '/apps/grpc/bench.proto"]'],
        "absent": ["-d opcache"],
    },
]

# The 128-byte asset of the static hit targets.
TINY_CSS = b"/* bench asset, micro tier: the response must be far smaller than the wire */\n.a { color: #1a2b3c; padding: 0 } /*-----------*/\n"

BODY_CASES = [
    {
        "name": "hello expected body",
        "path": "apps/hello/expect.txt",
        # 24 bytes: the body of GET /?name=you on every hello, symfony, and laravel target.
        "expected": b"Hello from worker, you!\n",
    },
    {
        "name": "static asset of 128 bytes",
        "path": "apps/static/tiny.css",
        "expected": TINY_CSS,
    },
    {
        "name": "static hit expected body is the asset",
        "path": "apps/static/tiny.expect",
        "expected": TINY_CSS,
    },
]

# Spec section 3.4: the values of the shared php.ini.
PHP_INI = {
    "opcache.enable": "1",
    "opcache.enable_cli": "1",
    "opcache.validate_timestamps": "0",
    "opcache.jit": "disable",
    "opcache.memory_consumption": "256",
    "memory_limit": "256M",
    "realpath_cache_size": "4096K",
    "realpath_cache_ttl": "600",
    "expose_php": "0",
    "error_log": "/dev/stderr",
}


def render(path, values):
    text = (REPO / path).read_text()
    for name, value in values.items():
        text = text.replace("@@" + name + "@@", value)
    return text


def settings(text):
    # The config lines without the comment lines.
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", ";")))


def shared_block(path):
    # The lines from worker_processes to the first keepalive_requests line.
    lines = (REPO / path).read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("worker_processes"))
    end = next(i for i, line in enumerate(lines) if line.strip() == "keepalive_requests 1000000;")
    return lines[start : end + 1]


class TemplateTests(unittest.TestCase):
    def test_toml_templates(self):
        for case in TOML_CASES:
            with self.subTest(name=case["name"]):
                text = render(case["path"], case["values"])
                self.assertNotIn("@@", text)
                self.assertEqual(case["expected"], tomllib.loads(text))

    def test_text_templates(self):
        for case in TEXT_CASES:
            with self.subTest(name=case["name"]):
                text = render(case["path"], case["values"])
                self.assertNotIn("@@", text)
                text = settings(text)
                for part in case["present"]:
                    self.assertIn(part, text)
                for part in case["absent"]:
                    self.assertNotIn(part, text)

    def test_nginx_templates_share_the_http_settings(self):
        self.assertEqual(shared_block("servers/nginx/rapira.conf.tpl"), shared_block("servers/nginx/fpm.conf.tpl"))

    def test_php_ini_values(self):
        values = {}
        for line in (REPO / "servers/php.ini").read_text().splitlines():
            if line and not line.startswith(";"):
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip()
        self.assertEqual(PHP_INI, values)

    def test_expected_bodies(self):
        for case in BODY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(case["expected"], (REPO / case["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
