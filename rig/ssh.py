"""ssh and scp to the rig boxes."""

import io
import shlex
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

USER = "fedora"
KNOWN_HOSTS = ".ssh-known-hosts"
RIG_DIR = "bench-rig"
OPTIONS = (
    "StrictHostKeyChecking=accept-new",
    f"UserKnownHostsFile={KNOWN_HOSTS}",
    "ControlMaster=auto",
    "ControlPath=.ssh-cm-%h",
    "ControlPersist=10m",
    "ConnectTimeout=5",
    "LogLevel=ERROR",
)
TAIL_LINES = 20

_key = Path("terraform/rig-key.pem")


@dataclass(frozen=True)
class Host:
    name: str
    public_ip: str
    private_ip: str


class SshError(RuntimeError):
    """A remote command failed. The message names the host."""


def set_key(path: Path) -> None:
    """Use this private key for every later ssh and scp call."""
    global _key
    _key = path


def _options() -> list[str]:
    argv = ["-i", str(_key), "-o", f"User={USER}"]
    for option in OPTIONS:
        argv += ["-o", option]
    return argv


def ssh_argv(host: Host, cmd: str) -> list[str]:
    """The ssh command line that runs cmd on host."""
    return ["ssh", *_options(), host.public_ip, cmd]


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


def run(host: Host, cmd: str, *, timeout: float | None = None, stdin: bytes | None = None) -> str:
    """Run cmd on host and return its stdout. Raise SshError on a nonzero exit or a timeout."""
    # Without input the remote command reads /dev/null, so parallel calls do not read the terminal.
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(ssh_argv(host, cmd), capture_output=True, timeout=timeout, **feed)
    except subprocess.TimeoutExpired as exc:
        raise SshError(f"{host.name}: timeout after {timeout} s: {cmd}") from exc
    out = proc.stdout.decode(errors="replace")
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")
        raise SshError(f"{host.name}: exit {proc.returncode}: {cmd}\n{_tail(err or out)}")
    return out


def run_many(jobs: list[tuple[Host, str]], *, timeout: float | None = None) -> list[str | SshError]:
    """Run the jobs in parallel. Each result is the stdout or the SshError, in job order."""

    def one(job: tuple[Host, str]) -> str | SshError:
        try:
            return run(job[0], job[1], timeout=timeout)
        except SshError as exc:
            return exc

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(one, jobs))


def wait_ssh(host: Host, *, tries: int = 60, delay_s: float = 5) -> None:
    """Wait until host accepts ssh. Raise SshError after the last try."""
    for _ in range(tries - 1):
        try:
            run(host, "true", timeout=30)
            return
        except SshError:
            time.sleep(delay_s)
    run(host, "true", timeout=30)


def tree_files(root: Path) -> list[str]:
    """Tracked and untracked files of the git tree at root, without ignored and deleted files."""

    def git(*args: str) -> set[str]:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *args], capture_output=True, check=True)
        return {name for name in out.stdout.decode().split("\0") if name}

    return sorted(git("-co", "--exclude-standard") - git("-d"))


def tree_tar(root: Path) -> bytes:
    """A gzip tar of the tree_files of root, with their mode bits."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in tree_files(root):
            tar.add(root / name, arcname=name, recursive=False)
    return buf.getvalue()


def _unpack(host: Host, data: bytes, dest: str) -> None:
    q = shlex.quote(dest)
    run(host, f"rm -rf {q} && mkdir -p {q} && tar -xzf - -C {q}", stdin=data)


def stage_dir(root: Path, host: Host, dest: str) -> None:
    """Replace ~/dest on host with the git tree at root."""
    _unpack(host, tree_tar(root), dest)


def stage_tree(hosts: list[Host]) -> None:
    """Copy this repository tree to ~/bench-rig on each host."""
    data = tree_tar(Path("."))
    for host in hosts:
        _unpack(host, data, RIG_DIR)


def copy_from(host: Host, remote: str, local: Path) -> None:
    """Copy one remote file to local with scp."""
    local.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["scp", "-q", *_options(), f"{host.public_ip}:{remote}", str(local)], capture_output=True)
    if proc.returncode != 0:
        raise SshError(f"{host.name}: scp {remote} failed\n{_tail(proc.stderr.decode(errors='replace'))}")
