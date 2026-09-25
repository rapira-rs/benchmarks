"""The rig from the terraform outputs, TTL handling, and provisioning."""

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rig import ssh
from rig.ssh import Host, SshError

DEADLINE_FILE = "/etc/rapira-bench-deadline"
TTL_ARM = "/usr/local/sbin/rapira-bench-ttl-arm"
TTL_MARGIN_MIN = 15


@dataclass(frozen=True)
class Rig:
    server: Host
    loaders: tuple[Host, ...]
    server_type: str
    loader_type: str
    ami_id: str
    key_file: Path

    @property
    def hosts(self) -> list[Host]:
        return [self.server, *self.loaders]


def from_terraform(tf_dir: Path) -> Rig:
    """Read the rig from `terraform output -json` and select its key for ssh."""
    proc = subprocess.run(["terraform", f"-chdir={tf_dir}", "output", "-json"], capture_output=True, text=True)
    outputs = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
    if "server_public_ip" not in outputs:
        raise RuntimeError("no rig: terraform has no outputs; run 'make up' first")
    value = {name: item["value"] for name, item in outputs.items()}
    key_file = tf_dir / value["key_file"]
    if not key_file.is_file():
        raise RuntimeError(f"no rig: {key_file} is missing; run 'make up' first")
    loaders = tuple(
        Host(f"loader-{i}", public_ip, private_ip)
        for i, (public_ip, private_ip) in enumerate(zip(value["loader_public_ips"], value["loader_private_ips"]), start=1)
    )
    ssh.set_key(key_file)
    return Rig(
        server=Host("server", value["server_public_ip"], value["server_private_ip"]),
        loaders=loaders,
        server_type=value["server_instance_type"],
        loader_type=value["loader_instance_type"],
        ami_id=value["ami_id"],
        key_file=key_file,
    )


def remaining_ttl_s(host: Host) -> int:
    """Seconds until the TTL shutdown of host. 0 when the deadline is unreadable."""
    try:
        deadline = int(ssh.run(host, f"cat {DEADLINE_FILE}").strip())
    except (SshError, ValueError):
        return 0
    return deadline - int(time.time())


def arm_ttl(hosts: list[Host], minutes: int) -> None:
    """Set the TTL of every host to minutes from now."""
    for result in ssh.run_many([(host, f"sudo {TTL_ARM} {minutes}") for host in hosts]):
        if isinstance(result, SshError):
            raise result


def ensure_ttl(hosts: list[Host], needed_s: int) -> None:
    """Extend the TTL of each host that expires before needed_s. AUTO_EXTEND=0 makes a short TTL an error."""
    for host in hosts:
        left = remaining_ttl_s(host)
        if left >= needed_s:
            continue
        if os.environ.get("AUTO_EXTEND", "1") != "1":
            raise RuntimeError(f"TTL on {host.name} expires in {left} s, the run needs about {needed_s} s; run 'make extend TTL=<minutes>'")
        minutes = needed_s // 60 + TTL_MARGIN_MIN
        print(f"==> TTL on {host.name} has {left} s left, the run needs about {needed_s} s; extending to {minutes} min")
        arm_ttl([host], minutes)


def provision(rig: Rig, *, ttl_min: int, server_env: dict[str, str]) -> None:
    """Prepare every box, stage the tree, and run the provisioning scripts in parallel."""
    for host in rig.hosts:
        ssh.wait_ssh(host)
    for result in ssh.run_many([(host, "sudo cloud-init status --wait >/dev/null") for host in rig.hosts]):
        if isinstance(result, SshError):
            raise result
    arm_ttl(rig.hosts, ttl_min)
    ssh.stage_tree(rig.hosts)
    print(f"==> provisioning {len(rig.hosts)} boxes")
    env = " ".join(f"{name}={shlex.quote(value)}" for name, value in server_env.items())
    jobs = [(rig.server, f"{env} bash {ssh.RIG_DIR}/box/provision-server.sh")]
    jobs += [(loader, f"bash {ssh.RIG_DIR}/box/provision-loader.sh") for loader in rig.loaders]
    failed = [result for result in ssh.run_many(jobs) if isinstance(result, SshError)]
    if failed:
        raise SshError("provisioning failed; the rig still bills, fix and rerun 'make provision' or run 'make down'\n" + "\n".join(str(exc) for exc in failed))
