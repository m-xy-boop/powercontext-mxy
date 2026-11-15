"""Ubuntu 22.04 x86_64: prepare and supervise a real Dify stack in user space.

Usage: python3 scripts/native.py prepare; python3 scripts/native.py up
No sudo, containers, model API key or global Python installs are required.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = Path(os.getenv("ECHO_DEPLOY_ROOT", str(ROOT / ".runtime/deploy")))
UV = os.getenv("UV_BIN", shutil.which("uv") or str(Path.home() / ".local/bin/uv"))
CHILDREN = []
PACKAGES = [
    "postgresql-14",
    "postgresql-client-14",
    "libllvm14",
    "libpq5",
    "redis-server",
    "redis-tools",
    "libjemalloc2",
    "liblzf1",
    "liblua5.1-0",
    "libreadline8",
    "lua-cjson",
    "lua-bitop",
]


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def download(url, path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    print("下载", path.name, flush=True)
    run(
        [
            "curl",
            "-fL",
            "--retry",
            "3",
            "--connect-timeout",
            "25",
            "--max-time",
            "600",
            url,
            "-o",
            temporary,
        ]
    )
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def env():
    values = os.environ.copy()
    values.update(
        {
            "DEBUG": "false",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "15432",
            "DB_USERNAME": "dify",
            "DB_PASSWORD": "",
            "DB_DATABASE": "dify",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "16379",
            "REDIS_PASSWORD": "",
            "CELERY_BROKER_URL": "redis://127.0.0.1:16379/1",
            "STORAGE_TYPE": "opendal",
            "OPENDAL_SCHEME": "fs",
            "OPENDAL_FS_ROOT": str(DEPLOY / "storage"),
            "SSRF_PROXY_HTTP_URL": "",
            "SSRF_PROXY_HTTPS_URL": "",
            "ECHO_MODEL": "qwen2.5-1.5b-instruct",
            "ECHO_RUNTIME": str(ROOT / ".runtime"),
            "LD_LIBRARY_PATH": str(DEPLOY / "root/usr/lib/x86_64-linux-gnu"),
        }
    )
    secret_file = DEPLOY / "secrets.json"
    if not secret_file.exists():
        secret_file.write_text(
            json.dumps(
                {
                    "SECRET_KEY": secrets.token_hex(32),
                    "ECHO_TOOL_KEY": secrets.token_hex(24),
                }
            )
        )
        secret_file.chmod(0o600)
    values.update(json.loads(secret_file.read_text()))
    # Explicit overrides allow reuse of an existing local stack for development.
    for key in (
        "SECRET_KEY",
        "ECHO_TOOL_KEY",
        "DIFY_APP_API_KEY",
        "DIFY_API_URL",
        "ECHO_MODEL_URL",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def prepare():
    DEPLOY.mkdir(parents=True, exist_ok=True)
    if not Path(UV).exists() and shutil.which(UV) is None:
        raise SystemExit(
            "请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
        )
    package_dir = DEPLOY / "packages"
    package_dir.mkdir(exist_ok=True)
    if not (DEPLOY / "root/usr/lib/postgresql/14/bin/postgres").exists():
        run(["apt-get", "download", *PACKAGES], cwd=package_dir)
        for package in package_dir.glob("*.deb"):
            run(["dpkg-deb", "-x", package, DEPLOY / "root"])
    if not (DEPLOY / "dify-1.9.2/api/pyproject.toml").exists():
        download(
            "https://github.com/langgenius/dify/archive/refs/tags/1.9.2.tar.gz",
            DEPLOY / "dify.tar.gz",
        )
        with tarfile.open(DEPLOY / "dify.tar.gz") as archive:
            # Refuse archive paths outside the extraction directory.
            for member in archive.getmembers():
                if (
                    not (DEPLOY / member.name)
                    .resolve()
                    .is_relative_to(DEPLOY.resolve())
                ):
                    raise ValueError("unsafe source archive")
            archive.extractall(DEPLOY)
    api = DEPLOY / "dify-1.9.2/api"
    run([UV, "python", "install", "3.12"])
    run(
        [
            UV,
            "sync",
            "--frozen",
            "--no-default-groups",
            "--group",
            "storage",
            "--group",
            "tools",
            "--python",
            "3.12",
        ],
        cwd=api,
    )
    if not (ROOT / ".venv/bin/python").exists():
        run([UV, "venv", "--python", "3.12", ROOT / ".venv"])
    run(
        [
            UV,
            "pip",
            "sync",
            "--python",
            ROOT / ".venv/bin/python",
            ROOT / "requirements.lock",
        ]
    )
    model = DEPLOY / "models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    download(
        "https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        model,
    )
    if not (DEPLOY / "llama/build/bin/llama-server").exists():
        download(
            "https://github.com/ggml-org/llama.cpp/releases/download/b7132/llama-b7132-bin-ubuntu-x64.zip",
            DEPLOY / "llama.zip",
        )
        with zipfile.ZipFile(DEPLOY / "llama.zip") as archive:
            archive.extractall(DEPLOY / "llama")
        for executable in (DEPLOY / "llama/build/bin").glob("*"):
            executable.chmod(executable.stat().st_mode | 0o111)
    if not (DEPLOY / "pgdata/PG_VERSION").exists():
        run(
            [
                DEPLOY / "root/usr/lib/postgresql/14/bin/initdb",
                "-D",
                DEPLOY / "pgdata",
                "-U",
                "dify",
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
            ],
            env=env(),
        )
    hashes = {
        str(path.relative_to(DEPLOY)): sha256(path)
        for path in [model, DEPLOY / "llama/build/bin/llama-server"]
    }
    (DEPLOY / "assets-sha256.json").write_text(json.dumps(hashes, indent=2))
    print("依赖已就绪。运行 python3 scripts/native.py up")


def start(name, args, cwd, values):
    logs = ROOT / ".runtime/logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / (name + ".log")).open("a") as output:
        process = subprocess.Popen(
            [str(a) for a in args],
            cwd=cwd,
            env=values,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    CHILDREN.append(process)
    return process


def wait_url(url, seconds=90):
    for _ in range(seconds):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        if any(process.poll() is not None for process in CHILDREN):
            raise RuntimeError("服务启动失败，请查看 .runtime/logs/")
        time.sleep(1)
    raise RuntimeError("服务启动超时：" + url)


def up():
    values = env()
    api = DEPLOY / "dify-1.9.2/api"
    pg = DEPLOY / "root/usr/lib/postgresql/14/bin"
    # Keep supervision ownership clear; ports must be unused at startup.
    import socket

    for port in (15432, 16379, 15001, 18080, 18081):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"端口 {port} 已占用，请先停止旧实例。")
    start(
        "postgres",
        [
            pg / "postgres",
            "-D",
            DEPLOY / "pgdata",
            "-p",
            "15432",
            "-k",
            DEPLOY,
            "-h",
            "127.0.0.1",
        ],
        ROOT,
        values,
    )
    start(
        "redis",
        [
            DEPLOY / "root/usr/bin/redis-server",
            "--port",
            "16379",
            "--bind",
            "127.0.0.1",
            "--dir",
            DEPLOY,
        ],
        ROOT,
        values,
    )
    for _ in range(40):
        check = subprocess.run(
            [str(pg / "pg_isready"), "-h", "127.0.0.1", "-p", "15432"],
            env=values,
            capture_output=True,
        )
        if check.returncode == 0:
            break
        time.sleep(0.5)
    databases = subprocess.check_output(
        [
            str(pg / "psql"),
            "-h",
            "127.0.0.1",
            "-p",
            "15432",
            "-U",
            "dify",
            "-d",
            "postgres",
            "-Atc",
            "SELECT datname FROM pg_database",
        ],
        env=values,
        text=True,
    )
    if "dify" not in databases.splitlines():
        run(
            [pg / "createdb", "-h", "127.0.0.1", "-p", "15432", "-U", "dify", "dify"],
            env=values,
        )
    run([api / ".venv/bin/flask", "db", "upgrade"], cwd=api, env=values)
    start(
        "model",
        [
            DEPLOY / "llama/build/bin/llama-server",
            "-m",
            DEPLOY / "models/qwen2.5-1.5b-instruct-q4_k_m.gguf",
            "--host",
            "127.0.0.1",
            "--port",
            "18081",
            "-c",
            "4096",
            "-t",
            "4",
            "--parallel",
            "1",
            "--alias",
            "qwen2.5-1.5b-instruct",
        ],
        ROOT,
        values,
    )
    start(
        "dify",
        [
            api / ".venv/bin/flask",
            "run",
            "--host",
            "127.0.0.1",
            "--port",
            "15001",
            "--no-reload",
        ],
        api,
        values,
    )
    start(
        "echo",
        [
            ROOT / ".venv/bin/python",
            "-m",
            "uvicorn",
            "echoes.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18080",
            "--workers",
            "1",
        ],
        ROOT,
        values,
    )
    for port in (18081, 18080):
        wait_url(f"http://127.0.0.1:{port}/health")
    wait_url("http://127.0.0.1:15001/health")
    credential = ROOT / ".runtime/dify-credential.json"
    if not credential.exists():
        run(
            [api / ".venv/bin/python", ROOT / "scripts/import_native.py"],
            cwd=api,
            env=values,
        )
    print(
        "回声当铺已营业：http://127.0.0.1:18080 （Ctrl+C 停止本次启动的服务）",
        flush=True,
    )
    while all(process.poll() is None for process in CHILDREN):
        time.sleep(1)
    raise RuntimeError("有服务退出，请查看 .runtime/logs/")


def shutdown(*_):
    raise KeyboardInterrupt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "up"])
    command = parser.parse_args().command
    signal.signal(signal.SIGTERM, shutdown)
    try:
        prepare() if command == "prepare" else up()
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(CHILDREN):
            if process.poll() is None:
                process.terminate()
        for process in CHILDREN:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
