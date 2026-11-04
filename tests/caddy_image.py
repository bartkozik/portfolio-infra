import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def docker(*arguments):
    return subprocess.check_output(["docker", *arguments], text=True).strip()


def main(image):
    build = docker("run", "--rm", image, "caddy", "build-info")
    match = re.search(r"^go\s+go(\d+)\.(\d+)\.(\d+)\b", build, re.MULTILINE)
    if not match:
        raise RuntimeError("Cannot determine the Caddy Go toolchain")
    version = tuple(map(int, match.groups()))
    fixed = (version[:2] == (1, 26) and version[2] >= 10) or version >= (1, 27, 3)
    if not fixed:
        raise RuntimeError(f"Go {version} predates the compromised CA key blocklist")
    print(f"PASS: Caddy uses Go {'.'.join(map(str, version))}")
    name = "caddy-security-test-" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory() as temporary:
        config = Path(temporary) / "Caddyfile"
        config.write_text('''{
    auto_https off
}
:8080 {
    respond /healthz "healthy"
}
:8081 {
    header X-Proxy-Test "passed"
    reverse_proxy 127.0.0.1:8080
}
''')
        docker("run", "--rm", "-v", f"{temporary}:/etc/caddy:ro", image,
               "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        try:
            docker("run", "-d", "--name", name, "--read-only", "--cap-drop", "ALL",
                   "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges:true",
                   "--tmpfs", "/data", "--tmpfs", "/config", "--tmpfs", "/tmp",
                   "-v", f"{temporary}:/etc/caddy:ro", image)
            for attempt in range(20):
                try:
                    response = docker("exec", name, "wget", "-qO-", "http://127.0.0.1:8081/healthz")
                    if response == "healthy":
                        break
                except subprocess.CalledProcessError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Caddy did not proxy the health check")
            config.write_text(config.read_text().replace('"healthy"', '"reloaded"'))
            docker("exec", name, "caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
            response = docker("exec", name, "wget", "-qO-", "http://127.0.0.1:8081/healthz")
            if response != "reloaded":
                raise RuntimeError("Caddy reload did not update the proxied response")
            print("PASS: restricted container, config validation, HTTP proxy and reload")
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main(sys.argv[1])
