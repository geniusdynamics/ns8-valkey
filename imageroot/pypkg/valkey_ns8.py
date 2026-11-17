#
# Copyright (C) 2026 Genius Dynamics
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Shared helpers of the ns8-valkey actions, events and commands.

Secrets (the admin and replication passwords, the cluster CA key) live in
0600 files under the state directory. They are never written to the module
environment, the cluster Redis, argv or logs.

Topology management talks to Valkey directly with redis-py (the core
agent environment ships it), as the "ns8admin" ACL user.
"""

import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sys
import time

ADMIN_USER = "ns8admin"
REPL_USER = "ns8repl"
CLIENT_PREFIX = "ns8c-"
SERVICE = "valkey"
CLUSTER_SERVICE = "valkey-cluster"
EVENT_CHANGED = "valkey-changed"
EVENT_CLUSTER_CHANGED = "valkey-cluster-changed"
ROLES = ("standalone", "initial", "join")
CLUSTER_ROLES = ("initial", "join")
PROCS = ("main", "replica")
UNITS = {"main": "valkey.service", "replica": "valkey-replica.service"}
CONTAINERS = {"main": "valkey", "replica": "valkey-replica"}
TOTAL_SLOTS = 16384
NODE_TIMEOUT_MS = 10000
KEY_PREFIX_RE = re.compile(r"^[A-Za-z0-9:_.{}-]{0,64}$")
CLIENT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")


def log(*args):
    print(*args, file=sys.stderr)


def _agent():
    import agent
    return agent


def warning(msg):
    log(_agent().SD_WARNING + msg)


# ---------------------------------------------------------------------------
# State files and secrets
# ---------------------------------------------------------------------------

def state_dir():
    return os.environ.get("AGENT_STATE_DIR") or os.getcwd()


def state_path(*parts):
    return os.path.join(state_dir(), *parts)


SECRETS_FILE = "secrets.env"
TLS_DIR = "tls"


def write_private(path, text, mode=0o600):
    """Atomic write of a file that is created with the given mode"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as fp:
            fp.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_envfile(path):
    values = {}
    try:
        with open(path) as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key] = value
    except FileNotFoundError:
        pass
    return values


def read_secrets():
    return read_envfile(state_path(SECRETS_FILE))


def write_secrets(values):
    text = "".join(f"{k}={v}\n" for k, v in sorted(values.items()))
    write_private(state_path(SECRETS_FILE), text)


def new_password():
    # Hexadecimal: safe in URLs and config files without escaping
    return secrets.token_hex(32)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ensure_secrets():
    values = read_secrets()
    changed = False
    for key in ("VALKEY_ADMIN_PASSWORD", "VALKEY_REPL_PASSWORD"):
        if not values.get(key):
            values[key] = new_password()
            changed = True
    if changed:
        write_secrets(values)
    return values


def admin_password():
    return read_secrets().get("VALKEY_ADMIN_PASSWORD", "")


# ---------------------------------------------------------------------------
# Environment and endpoints
# ---------------------------------------------------------------------------

def env(name, default=""):
    return os.environ.get(name, default)


def role():
    value = env("VALKEY_ROLE")
    return value if value in ROLES else ""


def is_cluster(value=None):
    return (role() if value is None else value) in CLUSTER_ROLES


def ha_mode(value=None):
    return "cluster" if is_cluster(value) else "standalone"


CONSISTENCY_MODES = ("availability", "consistency")
# min-replicas-max-lag: a replica counts while its last ack is at most this
# many seconds old. cluster-replica-validity-factor 10 (Valkey default):
# a replica disconnected for more than 10 x node timeout does not take over.
CONSISTENCY_SETTINGS = {
    "availability": {"min-replicas-to-write": "0", "min-replicas-max-lag": "10",
                     "cluster-replica-validity-factor": "0"},
    "consistency": {"min-replicas-to-write": "1", "min-replicas-max-lag": "10",
                    "cluster-replica-validity-factor": "10"},
}


def consistency():
    value = env("VALKEY_CONSISTENCY", "availability")
    return value if value in CONSISTENCY_MODES else "availability"


def consistency_settings(mode=None):
    """Valkey settings of the consistency mode; standalone servers have
    no replica, so they always use the availability settings"""
    if not is_cluster():
        return CONSISTENCY_SETTINGS["availability"]
    return CONSISTENCY_SETTINGS[mode or consistency()]


def apply_consistency_runtime(mode, endpoints=None):
    """CONFIG SET the settings of a mode on local (or given) processes"""
    settings = CONSISTENCY_SETTINGS[mode]
    eps = endpoints or [f"127.0.0.1:{ports()[p][0]}" for p in PROCS]
    for ep in eps:
        host, _, port = ep.rpartition(":")
        try:
            c = connect(host, int(port), timeout=3)
            for key, value in settings.items():
                c.config_set(key, value)
        except Exception as ex:
            warning(f"Cannot apply the {mode} settings on {ep}: {type(ex).__name__}")


def cluster_id():
    return env("VALKEY_CLUSTER_ID") if is_cluster() else ""


def ports():
    """{"main": (port, bus), "replica": (port, bus)} from the module TCP ports"""
    values = [int(p) for p in env("TCP_PORTS").split(",") if p.strip().isdigit()]
    if not values and env("TCP_PORT").isdigit():
        values = [int(env("TCP_PORT"))]
    while len(values) < 4:
        values.append(0)
    return {"main": (values[0], values[1]), "replica": (values[2], values[3])}


_vpn_cache = {}


def vpn_ip(node_id=None, rdb=None):
    """VPN address of a node (the local one by default), "" if unknown"""
    node_id = str(node_id or env("NODE_ID"))
    if node_id in _vpn_cache:
        return _vpn_cache[node_id]
    value = ""
    try:
        rdb = rdb or _agent().redis_connect(use_replica=True)
        value = rdb.hget(f"node/{node_id}/vpn", "ip_address") or ""
        ipaddress.ip_address(value)
    except Exception as ex:
        if value:
            warning(f"Invalid VPN address of node {node_id}: {ex}")
        value = ""
    _vpn_cache[node_id] = value
    return value


def local_host():
    return vpn_ip() or "127.0.0.1"


def valkey_version():
    image = env("VALKEY_IMAGE")
    tag = image.rsplit(":", 1)[-1] if ":" in image.rsplit("/", 1)[-1] else ""
    return tag.split("-", 1)[0]


def version_tuple(text):
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:3]) if parts else ()


# ---------------------------------------------------------------------------
# Valkey connections
# ---------------------------------------------------------------------------

def connect(host, port, password=None, user=ADMIN_USER, timeout=3.0):
    import redis
    return redis.Redis(host=host, port=int(port), username=user,
                       password=password if password is not None else admin_password(),
                       socket_timeout=timeout, socket_connect_timeout=timeout,
                       decode_responses=True)


def local_conn(proc="main", timeout=3.0):
    return connect("127.0.0.1", ports()[proc][0], timeout=timeout)


def ping(conn):
    try:
        return bool(conn.ping())
    except Exception:
        return False


def wait_ready(proc="main", timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = local_conn(proc, timeout=2)
            if conn.ping():
                info = conn.info("persistence")
                if not int(info.get("loading", 0)):
                    return conn
        except Exception:
            pass
        time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# ACL
# ---------------------------------------------------------------------------

def system_acl(values):
    """ACL lines of the users managed by the module itself. The default
    user is disabled. Passwords appear as SHA-256 hashes only."""
    admin = sha256(values["VALKEY_ADMIN_PASSWORD"])
    repl = sha256(values["VALKEY_REPL_PASSWORD"])
    return (
        "user default off resetkeys resetchannels -@all\n"
        f"user {ADMIN_USER} on #{admin} ~* &* +@all\n"
        # Replication and atomic slot migration: the target of a migration
        # replays the data commands (SELECT, RESTORE...) as this user
        f"user {REPL_USER} on #{repl} ~* &* +@all\n"
    )


def client_username(client_id):
    return CLIENT_PREFIX + client_id


def client_rules(password_hash, key_prefix=""):
    pattern = (key_prefix or "") + "*"
    return ["reset", "on", f"#{password_hash}", f"~{pattern}", "resetchannels", f"&{pattern}",
            "+@all", "-@admin", "-@dangerous"]


def list_clients(conn):
    """[{client_id, username, key_prefix}] from the ACL of a server"""
    clients = []
    for line in conn.execute_command("ACL", "LIST"):
        fields = line.split()
        if len(fields) < 2 or not fields[1].startswith(CLIENT_PREFIX):
            continue
        prefix = ""
        for field in fields[2:]:
            if field.startswith("~"):
                prefix = field[1:].removesuffix("*")
        clients.append({"client_id": fields[1][len(CLIENT_PREFIX):], "username": fields[1],
                        "key_prefix": prefix})
    return sorted(clients, key=lambda c: c["client_id"])


def client_acl_lines(conn):
    """{username: full ACL LIST line} of the client users"""
    result = {}
    for line in conn.execute_command("ACL", "LIST"):
        fields = line.split()
        if len(fields) >= 2 and fields[1].startswith(CLIENT_PREFIX):
            result[fields[1]] = line
    return result


def apply_client_line(conn, line):
    fields = line.split()
    conn.execute_command("ACL", "SETUSER", fields[1], "reset", *fields[2:])


def save_acl(conn):
    try:
        conn.execute_command("ACL", "SAVE")
    except Exception as ex:
        warning(f"ACL SAVE failed: {ex}")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def cluster_records(rdb=None, wanted=None):
    """{module_id: record} of the valkey-cluster service providers"""
    rdb = rdb or _agent().redis_connect(use_replica=True)
    result = {}
    for rec in _agent().list_service_providers(rdb, CLUSTER_SERVICE, "tcp"):
        if wanted is not None and rec.get("cluster_id") != wanted:
            continue
        result[rec["module_id"]] = rec
    return result


def member_sort_key(module_id):
    digits = re.findall(r"\d+$", module_id)
    return (int(digits[0]) if digits else 0, module_id)


def members(rdb=None, wanted=None):
    """Members of a cluster, ordered by module sequence number. Each is
    {"module_id", "node", "host", "procs": {"main": (port, bus), ...}}"""
    wanted = wanted if wanted is not None else cluster_id()
    result = []
    for module_id, rec in cluster_records(rdb, wanted).items():
        try:
            result.append({
                "module_id": module_id,
                "node": str(rec.get("node") or ""),
                "host": rec["host"],
                "allow_same_node": rec.get("allow_same_node") == "true",
                "procs": {
                    "main": (int(rec["port"]), int(rec["bus_port"])),
                    "replica": (int(rec["replica_port"]), int(rec["replica_bus_port"])),
                },
            })
        except (KeyError, ValueError):
            warning(f"Ignoring the malformed {CLUSTER_SERVICE} record of {module_id}")
    result.sort(key=lambda m: member_sort_key(m["module_id"]))
    return result


def cluster_candidates(rdb=None):
    """[{cluster_id, members: [module_id], nodes: [node]}] of the
    existing clusters, for the "join" role"""
    clusters = {}
    for module_id, rec in cluster_records(rdb).items():
        cid = rec.get("cluster_id")
        if not cid or module_id == env("MODULE_ID"):
            continue
        entry = clusters.setdefault(cid, {"cluster_id": cid, "members": [], "nodes": []})
        entry["members"].append(module_id)
        if rec.get("node") and rec["node"] not in entry["nodes"]:
            entry["nodes"].append(str(rec["node"]))
    for entry in clusters.values():
        entry["members"].sort(key=member_sort_key)
        entry["nodes"].sort()
    return sorted(clusters.values(), key=lambda c: c["cluster_id"])


def publish_records(rdb=None, fire_events=True):
    """Publish (or remove) the service records of this instance. Returns
    True when the client-facing record changed."""
    agent = _agent()
    rdb = rdb or agent.redis_connect(privileged=True)
    module_id = env("MODULE_ID")
    key = f"module/{module_id}/srv/tcp/{SERVICE}"
    ckey = f"module/{module_id}/srv/tcp/{CLUSTER_SERVICE}"
    current = rdb.hgetall(key)
    host = local_host()
    p = ports()
    value = role()
    if not value:
        rdb.delete(key, ckey)
        return bool(current)
    record = {
        "host": host,
        "port": str(p["main"][0]),
        "node": env("NODE_ID"),
        "ha_mode": ha_mode(value),
        "cluster": "true" if is_cluster(value) else "false",
        "cluster_id": cluster_id(),
        "tls": "false",
        "version": valkey_version(),
        "consistency": consistency() if is_cluster(value) else "availability",
    }
    if is_cluster(value):
        crec = {
            "host": host,
            "port": str(p["main"][0]),
            "bus_port": str(p["main"][1]),
            "replica_port": str(p["replica"][0]),
            "replica_bus_port": str(p["replica"][1]),
            "node": env("NODE_ID"),
            "cluster_id": cluster_id(),
            "role": value,
            "allow_same_node": "true" if env("VALKEY_ALLOW_SAME_NODE") == "1" else "false",
            "consistency": consistency(),
        }
        trx = rdb.pipeline()
        trx.delete(ckey)
        trx.hset(ckey, mapping=crec)
        trx.execute()
        seeds = [f"{m['host']}:{m['procs']['main'][0]}" for m in members(rdb, cluster_id())]
        own = f"{host}:{p['main'][0]}"
        if own not in seeds:
            seeds.insert(0, own)
        record["nodes"] = ",".join(seeds)
    else:
        rdb.delete(ckey)
        record["nodes"] = f"{host}:{p['main'][0]}"
    changed = current != record
    if changed:
        trx = rdb.pipeline()
        trx.delete(key)
        trx.hset(key, mapping=record)
        trx.execute()
        if fire_events:
            publish_event(rdb, EVENT_CHANGED, {
                "module_id": module_id,
                "module_uuid": env("MODULE_UUID"),
                "cluster_id": cluster_id(),
                "ha_mode": ha_mode(value),
            })
    return changed


def unpublish_records(rdb=None):
    rdb = rdb or _agent().redis_connect(privileged=True)
    module_id = env("MODULE_ID")
    rdb.delete(f"module/{module_id}/srv/tcp/{SERVICE}", f"module/{module_id}/srv/tcp/{CLUSTER_SERVICE}")


def publish_event(rdb, name, payload):
    rdb.publish(f"{env('AGENT_ID')}/event/{name}", json.dumps(payload))


def fire_cluster_changed(reason, rdb=None, cid=None, **extra):
    rdb = rdb or _agent().redis_connect(privileged=True)
    publish_event(rdb, EVENT_CLUSTER_CHANGED, {
        "cluster_id": cid or cluster_id(),
        "module_id": env("MODULE_ID"),
        "reason": reason,
        **extra,
    })


# ---------------------------------------------------------------------------
# Cluster CA and node certificates (mutual TLS of the cluster bus)
# ---------------------------------------------------------------------------

def tls_path(name):
    return state_path(TLS_DIR, name)


def ensure_ca():
    """Create the cluster CA if missing. Returns (cert_pem, key_pem)."""
    if os.path.exists(tls_path("ca.crt")) and os.path.exists(tls_path("ca.key")):
        with open(tls_path("ca.crt")) as c, open(tls_path("ca.key")) as k:
            return c.read(), k.read()
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"ns8-valkey CA {env('MODULE_ID')}")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=36500))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    store_ca(cert_pem, key_pem)
    return cert_pem, key_pem


def store_ca(cert_pem, key_pem):
    write_private(tls_path("ca.key"), key_pem, 0o600)
    write_private(tls_path("ca.crt"), cert_pem, 0o644)
    for name in ("node.crt", "node.key"):
        try:
            os.unlink(tls_path(name))
        except FileNotFoundError:
            pass


def ensure_node_cert(host):
    """Node certificate signed by the cluster CA, for the cluster bus"""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    cert_path, key_path = tls_path("node.crt"), tls_path("node.key")
    ca_pem, ca_key_pem = ensure_ca()
    ca_cert = x509.load_pem_x509_certificate(ca_pem.encode())
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path) as fp:
            current = x509.load_pem_x509_certificate(fp.read().encode())
        try:
            sans = current.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            ips = [str(ip) for ip in sans.get_values_for_type(x509.IPAddress)]
        except x509.ExtensionNotFound:
            ips = []
        if current.issuer == ca_cert.subject and host in ips:
            return
    ca_key = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, env("MODULE_ID") or "valkey")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=36500))
            .add_extension(x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address(host)),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,
                                                  ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    write_private(key_path, key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()).decode(), 0o600)
    write_private(cert_path, cert.public_bytes(serialization.Encoding.PEM).decode(), 0o644)


# ---------------------------------------------------------------------------
# Configuration rendering (ExecStartPre)
# ---------------------------------------------------------------------------

# Container entry point. The client users (ns8c-*) of users.acl are kept,
# the users of the module are rewritten from system.acl. Markers drop the
# cluster identity (restore, clone, leaving a cluster) or the data and the
# client users (joining or leaving a cluster: they belong to the cluster). With the append-only file missing, as after a restore, the
# server starts from dump.rdb with AOF off: valkey-started turns AOF on
# again, which writes a new AOF from the loaded data.
START_SCRIPT = """#!/bin/sh
set -e
umask 077
cd /data
if [ -f /etc/valkey/reset-cluster ]; then
    rm -f /data/nodes.conf
fi
if [ -f /etc/valkey/reset-data ]; then
    rm -rf /data/appendonlydir /data/dump.rdb /data/nodes.conf /data/users.acl
fi
touch /data/users.acl
grep '^user ns8c-' /data/users.acl > /data/users.acl.new || :
cat /etc/valkey/system.acl >> /data/users.acl.new
mv -f /data/users.acl.new /data/users.acl
extra=""
if [ -f /data/dump.rdb ] && ! ls /data/appendonlydir/*.manifest >/dev/null 2>&1; then
    extra="--appendonly no"
fi
exec valkey-server /etc/valkey/valkey.conf $extra
"""

def _snapshot(cfgdir):
    result = {}
    if os.path.isdir(cfgdir):
        for name in sorted(os.listdir(cfgdir)):
            path = os.path.join(cfgdir, name)
            if os.path.isfile(path) and name not in ("reset-cluster", "reset-data"):
                with open(path, "rb") as fp:
                    data = fp.read()
                if name == "valkey.conf":
                    # The consistency settings are applied at runtime
                    # (CONFIG SET): changing them needs no restart
                    data = b"\n".join(line for line in data.splitlines()
                                       if not line.startswith((b"min-replicas-",
                                                               b"cluster-replica-validity-factor")))
                result[name] = hashlib.sha256(data).hexdigest()
    return result


def render_changed(proc):
    """Render the configuration; True when it differs from the previous one"""
    cfgdir = state_path(f"valkey-{proc}")
    before = _snapshot(cfgdir)
    render(proc)
    return before != _snapshot(cfgdir)


def render(proc):
    """Write state/valkey-<proc>/ with valkey.conf, system.acl, the TLS
    files and the start script markers. Returns the directory."""
    values = ensure_secrets()
    cfgdir = state_path(f"valkey-{proc}")
    os.makedirs(cfgdir, mode=0o700, exist_ok=True)
    os.chmod(cfgdir, 0o700)
    port, bus = ports()[proc]
    cluster = is_cluster()
    host = vpn_ip()
    binds = " ".join(dict.fromkeys(([host] if host else []) + ["127.0.0.1"]))
    lines = [
        "# Generated by ns8-valkey: changes are overwritten",
        f"bind {binds}",
        f"port {port}",
        "protected-mode no",
        "dir /data",
        "aclfile /data/users.acl",
        "appendonly yes",
        "appendfsync everysec",
        'save "3600 1 300 100 60 10000"',
        "maxmemory-policy noeviction",
        "tcp-keepalive 60",
        *[f"{k} {v}" for k, v in consistency_settings().items() if not k.startswith("cluster-")],
        f"masteruser {REPL_USER}",
        f"masterauth {values['VALKEY_REPL_PASSWORD']}",
    ]
    maxmemory = env("VALKEY_MAXMEMORY_MB", "0")
    if maxmemory.isdigit() and int(maxmemory) > 0:
        lines.append(f"maxmemory {int(maxmemory)}mb")
    if cluster:
        announce = host or "127.0.0.1"
        ensure_node_cert(announce)
        for name in ("ca.crt", "node.crt", "node.key"):
            with open(tls_path(name)) as fp:
                write_private(os.path.join(cfgdir, name), fp.read(), 0o600)
        lines += [
            "cluster-enabled yes",
            "cluster-config-file /data/nodes.conf",
            f"cluster-port {bus}",
            f"cluster-announce-ip {announce}",
            f"cluster-node-timeout {NODE_TIMEOUT_MS}",
            f"cluster-replica-validity-factor {consistency_settings()['cluster-replica-validity-factor']}",
            "cluster-require-full-coverage yes",
            "cluster-allow-replica-migration yes",
            "tls-cluster yes",
            "tls-replication no",
            "tls-auth-clients yes",
            "tls-cert-file /etc/valkey/node.crt",
            "tls-key-file /etc/valkey/node.key",
            "tls-ca-cert-file /etc/valkey/ca.crt",
        ]
    write_private(os.path.join(cfgdir, "valkey.conf"), "\n".join(lines) + "\n", 0o600)
    write_private(os.path.join(cfgdir, "system.acl"), system_acl(values), 0o600)
    write_private(os.path.join(cfgdir, "start.sh"), START_SCRIPT, 0o700)
    for marker, var in (("reset-cluster", "VALKEY_RESET_CLUSTER"), ("reset-data", "VALKEY_RESET_DATA")):
        path = os.path.join(cfgdir, marker)
        if env(var) == "1" or (proc == "replica" and marker == "reset-data" and env("VALKEY_RESET_REPLICA") == "1"):
            write_private(path, "1\n", 0o600)
        elif os.path.exists(path):
            os.unlink(path)
    return cfgdir


# ---------------------------------------------------------------------------
# Cluster topology
# ---------------------------------------------------------------------------

class Node:
    """One line of CLUSTER NODES"""

    def __init__(self, line):
        fields = line.split()
        self.id = fields[0]
        addr = fields[1].split(",")[0]
        hostport, _, self.bus = addr.partition("@")
        self.host, _, port = hostport.rpartition(":")
        self.port = int(port) if port.isdigit() else 0
        self.flags = set(fields[2].split(","))
        self.master_id = fields[3] if fields[3] != "-" else ""
        self.link = fields[7] if len(fields) > 7 else ""
        self.slots = []
        for item in fields[8:]:
            if item.startswith("["):
                continue  # slot being imported or migrated
            if "-" in item:
                a, b = item.split("-")
                self.slots.append((int(a), int(b)))
            elif item.isdigit():
                self.slots.append((int(item), int(item)))

    @property
    def endpoint(self):
        return f"{self.host}:{self.port}"

    @property
    def is_master(self):
        return "master" in self.flags

    @property
    def failed(self):
        return "fail" in self.flags or "noaddr" in self.flags

    @property
    def slot_count(self):
        return sum(b - a + 1 for a, b in self.slots)


def cluster_info(conn):
    text = conn.execute_command("CLUSTER", "INFO")
    return dict(line.strip().split(":", 1) for line in text.splitlines() if ":" in line)


def cluster_nodes(conn):
    return [Node(line) for line in conn.execute_command("CLUSTER", "NODES").splitlines() if line.strip()]


def expected_processes(member_list):
    """{endpoint: {"module_id", "node", "proc", "host", "port", "bus"}}"""
    result = {}
    for m in member_list:
        for proc in PROCS:
            port, bus = m["procs"][proc]
            result[f"{m['host']}:{port}"] = {"module_id": m["module_id"], "node": m["node"], "proc": proc,
                                             "host": m["host"], "port": port, "bus": bus}
    return result


class relaxed_writes:
    """Allow writes on a primary without replica for a moment (consistency
    mode sets min-replicas-to-write 1): FLUSHALL of a node that becomes an
    empty primary, and the target of a slot migration, which replays the
    data as writes and would refuse them"""

    def __init__(self, conn):
        self.conn = conn
        self.saved = None

    def __enter__(self):
        try:
            self.saved = self.conn.config_get("min-replicas-to-write").get("min-replicas-to-write")
            if self.saved not in (None, "0"):
                self.conn.config_set("min-replicas-to-write", "0")
        except Exception:
            self.saved = None
        return self.conn

    def __exit__(self, *args):
        if self.saved not in (None, "0"):
            try:
                self.conn.config_set("min-replicas-to-write", self.saved)
            except Exception as ex:
                warning(f"Cannot restore min-replicas-to-write: {type(ex).__name__}")


class Reconciler:
    """Bring a Valkey cluster to the layout of its NS8 members:

    - fewer than 3 members: one shard (all slots on one primary), every
      other process replicates it;
    - 3 or more members: one slot-owning primary per member, slots spread
      evenly, and each primary replicated by a process of another member
      (a ring), so any one member can fail.
    """

    def __init__(self, member_list, password=None, logger=log, wait=True):
        self.members = member_list
        self.password = password if password is not None else admin_password()
        self.log = logger
        self.wait = wait
        self.expected = expected_processes(member_list)
        self.conns = {}
        self.changes = []
        self.deferred = False

    # -- helpers ----------------------------------------------------------

    def conn(self, endpoint):
        if endpoint not in self.conns:
            info = self.expected.get(endpoint)
            host, _, port = endpoint.rpartition(":")
            c = connect(host, int(port), password=self.password, timeout=5)
            self.conns[endpoint] = c if ping(c) else None
        return self.conns[endpoint]

    def reachable(self):
        return [ep for ep in self.expected if self.conn(ep)]

    def view(self):
        """CLUSTER NODES from the first reachable expected process"""
        for ep in self.expected:
            c = self.conn(ep)
            if c:
                try:
                    return {n.endpoint: n for n in cluster_nodes(c)}
                except Exception as ex:
                    self.log(f"CLUSTER NODES failed on {ep}: {ex}")
        return {}

    def note(self, text):
        self.changes.append(text)
        self.log(text)

    def sleep(self, seconds):
        if self.wait:
            time.sleep(seconds)

    def module_of(self, node):
        info = self.expected.get(node.endpoint)
        return info["module_id"] if info else None

    def wait_converged(self, timeout=30):
        """Wait until every reachable process knows every other one"""
        deadline = time.time() + timeout
        eps = set(self.reachable())
        while time.time() < deadline:
            ok = True
            for ep in eps:
                try:
                    known = {n.endpoint for n in cluster_nodes(self.conn(ep)) if not n.failed}
                except Exception:
                    ok = False
                    break
                if not eps <= known:
                    ok = False
                    break
            if ok:
                return True
            self.sleep(1)
        return False

    # -- steps ------------------------------------------------------------

    def meet(self):
        eps = self.reachable()
        if not eps:
            return False
        view = self.view()
        base = None
        # MEET from a node that is already part of the cluster (holds or
        # replicates slots), so that fresh nodes join it, not vice versa
        for ep in eps:
            n = view.get(ep)
            if n and (n.slots or n.master_id):
                base = ep
                break
        base = base or eps[0]
        met = False
        for ep in eps:
            if ep == base:
                continue
            known = view.get(ep)
            if known and not known.failed and "handshake" not in known.flags:
                continue
            info = self.expected[ep]
            try:
                self.conn(base).execute_command("CLUSTER", "MEET", info["host"], info["port"], info["bus"])
                self.note(f"MEET {ep} from {base}")
                met = True
            except Exception as ex:
                self.log(f"CLUSTER MEET {ep} failed: {ex}")
        if met:
            self.wait_converged()
        return True

    def forget_stale(self):
        """Forget the nodes that belong to no member. A failed primary that
        still owns slots is first replaced by one of its replicas."""
        view = self.view()
        stale = [n for ep, n in view.items() if ep not in self.expected]
        for node in stale:
            if node.slots:
                if not node.failed:
                    self.log(f"{node.endpoint} is not a member but still serves slots: not forgotten")
                    continue
                replicas = [n for n in view.values() if n.master_id == node.id and not n.failed
                            and n.endpoint in self.expected]
                if not replicas:
                    self.log(f"{node.endpoint} failed with slots and no replica: data of its slots is lost")
                    continue
                try:
                    self.conn(replicas[0].endpoint).execute_command("CLUSTER", "FAILOVER", "TAKEOVER")
                    self.note(f"FAILOVER TAKEOVER of {node.endpoint} by {replicas[0].endpoint}")
                    self.sleep(3)
                except Exception as ex:
                    self.log(f"Takeover by {replicas[0].endpoint} failed: {ex}")
                    continue
            for ep in self.reachable():
                try:
                    self.conn(ep).execute_command("CLUSTER", "FORGET", node.id)
                except Exception as ex:
                    if "Unknown node" not in str(ex) and "myself" not in str(ex):
                        self.log(f"CLUSTER FORGET {node.id} on {ep}: {ex}")
            self.note(f"FORGET {node.endpoint} ({node.id})")

    def ensure_slots(self):
        """Assign the slots no node owns: all of them on a new cluster, or
        the rest after a standalone server with data became the initial
        node (at start Valkey claims only the slots of its keys). They go
        to the primary that owns the most slots, else to the main process
        of the first member."""
        view = self.view()
        owned = set()
        for n in view.values():
            for a, b in n.slots:
                owned.update(range(a, b + 1))
        if len(owned) == TOTAL_SLOTS:
            return
        owners = [n for ep, n in view.items() if ep in self.expected and n.is_master and n.slots
                  and not n.failed]
        if owners:
            ep = max(owners, key=lambda n: n.slot_count).endpoint
        else:
            first = self.members[0]
            ep = f"{first['host']}:{first['procs']['main'][0]}"
        c = self.conn(ep)
        if not c:
            return
        ranges = []
        for slot in range(TOTAL_SLOTS):
            if slot in owned:
                continue
            if ranges and ranges[-1][1] == slot - 1:
                ranges[-1][1] = slot
            else:
                ranges.append([slot, slot])
        args = [x for r in ranges for x in r]
        c.execute_command("CLUSTER", "ADDSLOTSRANGE", *args)
        self.note(f"ADDSLOTSRANGE {TOTAL_SLOTS - len(owned)} unassigned slots on {ep}")
        self.wait_state_ok(ep)

    def wait_state_ok(self, endpoint, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if cluster_info(self.conn(endpoint)).get("cluster_state") == "ok":
                    return True
            except Exception:
                pass
            self.sleep(1)
        return False

    def wait_failover(self, endpoint, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.conn(endpoint).execute_command("ROLE")[0] == "master":
                    return True
            except Exception:
                pass
            self.sleep(1)
        return False

    def make_empty_primary(self, node):
        c = self.conn(node.endpoint)
        if node.master_id:
            c.execute_command("CLUSTER", "REPLICATE", "NO", "ONE")
            self.note(f"REPLICATE NO ONE on {node.endpoint}")
        # Always write FLUSHALL to the append-only file and rewrite it: the
        # detached replica empties its dataset in memory only, and loading
        # the old AOF base at the next start would bring back stale keys
        with relaxed_writes(c):
            c.flushall()
        try:
            c.execute_command("BGREWRITEAOF")
        except Exception as ex:
            self.log(f"BGREWRITEAOF on {node.endpoint}: {ex}")
        self.sleep(1)

    def choose_primaries(self):
        """Return the endpoints that must own slots"""
        view = self.view()
        live = {ep: n for ep, n in view.items() if ep in self.expected and not n.failed}
        owners = [n for n in live.values() if n.is_master and n.slots]
        if len(self.members) < 3:
            if not owners:
                return []
            best = max(owners, key=lambda n: (n.slot_count, -member_sort_key(self.module_of(n))[0]))
            return [best.endpoint]
        by_member = {m["module_id"]: [] for m in self.members}
        for n in owners:
            by_member[self.module_of(n)].append(n)
        # Fail back: a member without primary takes over from a member
        # with two, with a graceful (no data loss) manual failover
        for module_id, prim in by_member.items():
            if prim:
                continue
            for n in live.values():
                if self.module_of(n) != module_id or not n.master_id:
                    continue
                master = next((x for x in owners if x.id == n.master_id), None)
                if master and len(by_member[self.module_of(master)]) > 1:
                    try:
                        self.conn(n.endpoint).execute_command("CLUSTER", "FAILOVER")
                        self.note(f"FAILOVER (fail back) on {n.endpoint}")
                        if self.wait_failover(n.endpoint):
                            by_member[self.module_of(master)].remove(master)
                            by_member[module_id].append(n)
                    except Exception as ex:
                        self.log(f"Fail back on {n.endpoint} failed: {ex}")
                    break
        view = self.view()
        live = {ep: n for ep, n in view.items() if ep in self.expected and not n.failed}
        targets = []
        for m in self.members:
            mine = [n for n in live.values() if self.module_of(n) == m["module_id"]]
            prim = sorted([n for n in mine if n.is_master and n.slots], key=lambda n: -n.slot_count)
            if prim:
                targets.append(prim[0].endpoint)
                continue
            if not mine:
                continue  # member down: its slots stay where they are
            mine.sort(key=lambda n: (bool(n.master_id), self.expected[n.endpoint]["proc"] != "main"))
            candidate = mine[0]
            try:
                self.make_empty_primary(candidate)
                targets.append(candidate.endpoint)
            except Exception as ex:
                self.log(f"Cannot prepare {candidate.endpoint} as primary: {ex}")
        return targets

    def migrate(self, source_ep, ranges, target_id):
        target = next((n for n in self.view().values() if n.id == target_id), None)
        tconn = self.conn(target.endpoint) if target else None
        if tconn:
            with relaxed_writes(tconn):
                return self._migrate(source_ep, ranges, target_id)
        return self._migrate(source_ep, ranges, target_id)

    def _migrate(self, source_ep, ranges, target_id):
        c = self.conn(source_ep)
        args = []
        for a, b in ranges:
            args += ["SLOTSRANGE", a, b, "NODE", target_id]
        c.execute_command("CLUSTER", "MIGRATESLOTS", *args)
        deadline = time.time() + 600
        while time.time() < deadline:
            jobs = c.execute_command("CLUSTER", "GETSLOTMIGRATIONS") or []
            states = []
            for job in jobs:
                d = dict(zip(job[::2], job[1::2]))
                if d.get("target_node") == target_id:
                    states.append(str(d.get("state")))
            if states and all(s in ("success", "failed", "cancelled") for s in states):
                if any(s != "success" for s in states):
                    raise RuntimeError(f"slot migration from {source_ep} ended with {states}")
                return
            self.sleep(0.5)
            if not self.wait:
                return
        raise RuntimeError(f"slot migration from {source_ep} timed out")

    def balance(self, targets):
        """Move slots so that only the targets own them, evenly"""
        view = self.view()
        nodes = {ep: n for ep, n in view.items() if ep in self.expected}
        tnodes = [nodes[ep] for ep in targets if ep in nodes]
        if not tnodes:
            return
        base, extra = divmod(TOTAL_SLOTS, len(tnodes))
        want = {n.endpoint: base + (1 if i < extra else 0) for i, n in enumerate(tnodes)}
        donors = []   # (endpoint, [slots to give])
        for ep, n in nodes.items():
            if not n.is_master or not n.slots or n.failed:
                continue
            slots = [s for a, b in n.slots for s in range(a, b + 1)]
            keep = want.get(ep, 0)
            if len(slots) > keep:
                donors.append((ep, slots[keep:]))
        needs = [(n, want[n.endpoint] - n.slot_count) for n in tnodes if n.slot_count < want[n.endpoint]]
        for target, need in needs:
            while need > 0 and donors:
                ep, slots = donors[0]
                take, rest = slots[:need], slots[need:]
                ranges = []
                for s in take:
                    if ranges and ranges[-1][1] == s - 1:
                        ranges[-1][1] = s
                    else:
                        ranges.append([s, s])
                self.migrate(ep, ranges, target.id)
                self.note(f"MIGRATESLOTS {len(take)} slots {ep} -> {target.endpoint}")
                need -= len(take)
                if rest:
                    donors[0] = (ep, rest)
                else:
                    donors.pop(0)

    def place_replicas(self, targets):
        view = self.view()
        nodes = {ep: n for ep, n in view.items() if ep in self.expected and not n.failed}
        tnodes = [nodes[ep] for ep in targets if ep in nodes]
        if not tnodes:
            return
        others = [n for ep, n in nodes.items() if ep not in targets]
        if not others:
            return
        cap = -(-len(others) // len(tnodes))
        count = {n.id: 0 for n in tnodes}
        order = [m["module_id"] for m in self.members]
        tmod = {n.id: self.module_of(n) for n in tnodes}
        pending = []
        for n in others:
            current = n.master_id if not n.is_master else ""
            ok = (current in count and count[current] < cap
                  and (tmod[current] != self.module_of(n) or len(set(tmod.values())) == 1))
            if ok:
                count[current] += 1
            else:
                pending.append(n)
        for n in pending:
            mine = self.module_of(n)
            idx = order.index(mine) if mine in order else 0
            ring = [order[(idx - k) % len(order)] for k in range(1, len(order) + 1)]
            best = None
            for module_id in ring:
                cands = [t for t in tnodes if tmod[t.id] == module_id and count[t.id] < cap]
                if cands and (module_id != mine or len(set(tmod.values())) == 1):
                    best = cands[0]
                    break
            if best is None:
                cands = [t for t in tnodes if count[t.id] < cap] or tnodes
                best = min(cands, key=lambda t: count[t.id])
            try:
                c = self.conn(n.endpoint)
                if n.is_master and n.slots:
                    self.log(f"{n.endpoint} still owns slots: not turned into a replica")
                    continue
                if n.is_master and c.dbsize():
                    with relaxed_writes(c):
                        c.flushall()
                c.execute_command("CLUSTER", "REPLICATE", best.id)
                count[best.id] += 1
                self.note(f"REPLICATE {n.endpoint} -> {best.endpoint}")
            except Exception as ex:
                self.log(f"CLUSTER REPLICATE on {n.endpoint} failed: {ex}")

    def run(self):
        if not self.reachable():
            self.log("No member of the cluster is reachable")
            return False
        self.meet()
        self.forget_stale()
        self.ensure_slots()
        view = self.view()
        down = [ep for ep in self.expected if not self.conn(ep) or (ep in view and view[ep].failed)]
        if down:
            # A member is down or restarting: Valkey fails over by itself.
            # Moving slots now would be undone when it comes back.
            self.log(f"Not rebalancing while processes are down: {', '.join(down)}")
            self.deferred = True
            return True
        targets = self.choose_primaries()
        if targets:
            self.balance(targets)
            self.place_replicas(targets)
        return True


def leave_cluster(member_list, module_id, password=None, logger=log):
    """Before a member stops for good: hand its slots over. Each primary of
    the leaving member is replaced by its replica on another member with a
    graceful failover; without such a replica its slots are migrated to a
    primary of another member. Returns False when slots stay behind."""
    rec = Reconciler(member_list, password=password, logger=logger)
    mine = {ep for ep, meta in rec.expected.items() if meta["module_id"] == module_id}
    for _ in range(3):
        view = rec.view()
        owners = [n for ep, n in view.items() if ep in mine and n.is_master and n.slots and not n.failed]
        if not owners:
            return True
        for node in owners:
            replicas = [n for ep, n in view.items() if n.master_id == node.id and not n.failed
                        and ep in rec.expected and ep not in mine]
            done = False
            for rep in replicas:
                try:
                    rec.conn(rep.endpoint).execute_command("CLUSTER", "FAILOVER")
                    rec.note(f"FAILOVER on {rep.endpoint} (leaving {node.endpoint})")
                    if rec.wait_failover(rep.endpoint):
                        done = True
                        break
                except Exception as ex:
                    logger(f"Failover on {rep.endpoint} failed: {ex}")
            if done:
                continue
            others = [n for ep, n in view.items() if ep in rec.expected and ep not in mine
                      and n.is_master and n.slots and not n.failed]
            if not others:
                others = [n for ep, n in view.items() if ep in rec.expected and ep not in mine
                          and n.is_master and not n.failed]
            if not others:
                logger(f"No other primary can take the slots of {node.endpoint}")
                return False
            target = min(others, key=lambda n: n.slot_count)
            try:
                rec.migrate(node.endpoint, [list(r) for r in node.slots], target.id)
                rec.note(f"MIGRATESLOTS all slots {node.endpoint} -> {target.endpoint}")
            except Exception as ex:
                logger(f"Slot migration from {node.endpoint} failed: {ex}")
        time.sleep(2)
    view = rec.view()
    return not any(n.slots for ep, n in view.items() if ep in mine and n.is_master)


def handover(member_list, module_id, password=None, logger=log, timeout=60):
    """Before a restart: hand the slots of this member's primaries over to
    their replicas on other members with a graceful failover (no data
    loss). Slots without such a replica stay (a short outage)."""
    rec = Reconciler(member_list, password=password, logger=logger)
    mine = {ep for ep, meta in rec.expected.items() if meta["module_id"] == module_id}
    view = rec.view()
    for ep in mine:
        node = view.get(ep)
        if not node or not node.is_master or not node.slots or node.failed:
            continue
        for rep in [n for e, n in view.items() if n.master_id == node.id and not n.failed
                    and e in rec.expected and e not in mine]:
            try:
                info = rec.conn(rep.endpoint).info("replication")
                if info.get("master_link_status") != "up":
                    continue
                rec.conn(rep.endpoint).execute_command("CLUSTER", "FAILOVER")
                rec.note(f"FAILOVER on {rep.endpoint} before restarting {ep}")
                if rec.wait_failover(rep.endpoint, timeout):
                    break
            except Exception as ex:
                logger(f"Failover on {rep.endpoint} failed: {ex}")
    return rec.changes


def cluster_client(member_list, password=None, timeout=5):
    """redis-py cluster client as the admin user, or None"""
    from redis.cluster import RedisCluster, ClusterNode
    seeds = [ClusterNode(m["host"], m["procs"]["main"][0]) for m in member_list]
    try:
        return RedisCluster(startup_nodes=seeds, username=ADMIN_USER,
                            password=password if password is not None else admin_password(),
                            socket_timeout=timeout, socket_connect_timeout=timeout,
                            decode_responses=True)
    except Exception as ex:
        warning(f"Cannot connect to the cluster: {type(ex).__name__}")
        return None


RESTART_LOCK = "ns8-valkey:{restart}:lock"


def acquire_restart_lock(client, owner, wait=900, ttl=900):
    """One member restarts at a time during updates. Returns True when held
    (or when the cluster cannot keep a lock: then nothing serializes)."""
    if not client:
        return True
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            if client.set(RESTART_LOCK, owner, nx=True, ex=ttl) or client.get(RESTART_LOCK) == owner:
                return True
        except Exception as ex:
            warning(f"Restart lock unavailable: {type(ex).__name__}")
            return True
        time.sleep(5)
    return False


def release_restart_lock(client, owner):
    if not client:
        return
    try:
        if client.get(RESTART_LOCK) == owner:
            client.delete(RESTART_LOCK)
    except Exception:
        pass


def wait_replication(proc, timeout=180):
    """Wait until a local replica process is in sync with its primary"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = local_conn(proc).info("replication")
            if info.get("role") != "slave" or info.get("master_link_status") == "up":
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def cluster_health(member_list, password=None):
    """Health report of a cluster (no secrets)"""
    expected = expected_processes(member_list)
    procs, view, state, info_ok = [], {}, "", False
    conns = {}
    for ep in expected:
        host, _, port = ep.rpartition(":")
        c = connect(host, int(port), password=password, timeout=2)
        conns[ep] = c if ping(c) else None
    for ep, c in conns.items():
        if c and not view:
            try:
                view = {n.endpoint: n for n in cluster_nodes(c)}
                state = cluster_info(c).get("cluster_state", "")
                info_ok = True
            except Exception:
                pass
    ids = {n.id: n for n in view.values()}
    for ep, meta in expected.items():
        n = view.get(ep)
        entry = {
            "module_id": meta["module_id"], "node": meta["node"], "process": meta["proc"],
            "endpoint": ep, "reachable": bool(conns.get(ep)),
            "role": "unknown", "slots": 0, "primary": "", "link": "", "lag_bytes": None, "lag_seconds": None,
        }
        if n:
            entry["role"] = "primary" if n.is_master else "replica"
            entry["slots"] = n.slot_count
            entry["failed"] = n.failed
            if n.master_id and n.master_id in ids:
                entry["primary"] = ids[n.master_id].endpoint
        c = conns.get(ep)
        if c:
            try:
                rep = c.info("replication")
                if rep.get("role") == "slave":
                    entry["link"] = rep.get("master_link_status", "")
                    pc = conns.get(entry["primary"])
                    if pc:
                        prep = pc.info("replication")
                        for key, val in prep.items():
                            if key.startswith("slave") and isinstance(val, dict) \
                                    and int(val.get("port", 0)) == int(ep.rpartition(":")[2]):
                                entry["lag_bytes"] = max(0, int(prep.get("master_repl_offset", 0)) - int(val.get("offset", 0)))
                                entry["lag_seconds"] = int(val.get("lag", 0))
            except Exception:
                pass
        procs.append(entry)
    shards = len([p for p in procs if p["role"] == "primary" and p["slots"]])
    n = len(member_list)
    tolerance = 1 if n >= 3 and all(
        any(r["primary"] == p["endpoint"] and r["module_id"] != p["module_id"] and r["reachable"]
            for r in procs) for p in procs if p["role"] == "primary" and p["slots"]) else 0
    warnings = []
    if n < 3:
        warnings.append("below_quorum")
    if not info_ok:
        warnings.append("unreachable")
    elif state != "ok":
        warnings.append("cluster_down")
    if any(not p["reachable"] for p in procs):
        warnings.append("members_down")
    if n >= 3 and not tolerance:
        warnings.append("no_redundancy")
    return {
        "members_count": n,
        "shards": shards,
        "cluster_state": state or "unknown",
        "quorum_ok": n >= 3 and state == "ok",
        "failure_tolerance": tolerance,
        "processes": procs,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Locks and coordinator
# ---------------------------------------------------------------------------

class FileLock:
    def __init__(self, name):
        self.path = state_path(f".{name}.lock")
        self.fd = None

    def acquire(self, blocking=True):
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def coordinator(member_list, password=None):
    """Module ID of the member that runs the reconciliation: the lowest
    module sequence number whose main process answers"""
    for m in member_list:
        port = m["procs"]["main"][0]
        if ping(connect(m["host"], port, password=password, timeout=2)):
            return m["module_id"]
    return ""


def reconcile_local(reason="", logger=log):
    """Run the reconciliation from this member (serialized by a lock)"""
    result = run_reconcile(reason, logger=logger)
    return result["changes"]


RECONCILE_KEY = "reconcile"


def record_reconcile(result):
    """Last reconciliation of this member, in its own Redis keyspace (no
    secrets): get-cluster-status shows the latest one of the cluster"""
    try:
        rdb = _agent().redis_connect(privileged=True)
        rdb.hset(f"module/{env('MODULE_ID')}/{RECONCILE_KEY}", mapping={
            "time": str(int(result["time"])),
            "trigger": result["trigger"][:64],
            "result": result["result"],
            "changes": str(len(result["changes"])),
            "module_id": env("MODULE_ID"),
        })
    except Exception as ex:
        warning(f"Cannot record the reconciliation: {type(ex).__name__}")


def last_reconcile(member_list):
    """Latest recorded reconciliation among the members, or {}"""
    try:
        rdb = _agent().redis_connect(use_replica=True)
    except Exception:
        return {}
    latest = {}
    for m in member_list:
        rec = rdb.hgetall(f"module/{m['module_id']}/{RECONCILE_KEY}") or {}
        if rec.get("time", "").isdigit() and int(rec["time"]) > int(latest.get("time", 0) or 0):
            latest = rec
    if latest:
        latest = {"time": int(latest["time"]), "trigger": latest.get("trigger", ""),
                  "result": latest.get("result", ""), "changes": int(latest.get("changes", "0") or 0),
                  "module_id": latest.get("module_id", "")}
    return latest


def restart_in_progress(member_list):
    """True while a member holds the rolling restart lock of an update"""
    client = cluster_client(member_list, timeout=3)
    if not client:
        return False
    try:
        return bool(client.get(RESTART_LOCK))
    except Exception:
        return False


def run_reconcile(trigger, logger=log, wait_lock=True, only_coordinator=False, quiet=False):
    """Reconcile from this member and record the outcome.

    wait_lock=False skips the run when another one holds the lock (the
    timer never overlaps an event-driven run). only_coordinator=True runs
    only on the coordinator. quiet=True keeps the log silent unless
    something is repaired or fails. Returns {"result", "changes", ...};
    result is one of ok (nothing to do), repaired, deferred (a process is
    down: only membership was checked), skipped (not run), error."""
    out = {"time": time.time(), "trigger": trigger, "result": "skipped", "changes": []}
    if not is_cluster():
        return out
    buffered = []
    sink = buffered.append if quiet else logger
    lock = FileLock("reconcile")
    if not lock.acquire(blocking=wait_lock):
        return out
    try:
        member_list = members()
        if only_coordinator and coordinator(member_list) != env("MODULE_ID"):
            return out
        if quiet and restart_in_progress(member_list):
            return out  # an update restarts members one by one: let it finish
        rec = Reconciler(member_list, logger=sink)
        try:
            rec.run()
            out["result"] = "repaired" if rec.changes else ("deferred" if rec.deferred else "ok")
        except Exception as ex:
            out["result"] = "error"
            buffered.append(f"Reconciliation failed: {ex}")
            if not quiet:
                logger(buffered[-1])
        out["changes"] = rec.changes
        if quiet and out["result"] in ("repaired", "error"):
            for line in buffered:
                logger(line)
        record_reconcile(out)
        return out
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# ACL distribution
# ---------------------------------------------------------------------------

def all_process_endpoints():
    """host:port of every Valkey process this instance must keep in sync:
    the local processes, plus every process of the cluster members"""
    if not is_cluster():
        return [f"127.0.0.1:{ports()['main'][0]}"]
    return list(expected_processes(members()).keys())


def for_each_process(fn):
    """Call fn(conn) on every reachable process; returns (ok, failed) lists"""
    ok, failed = [], []
    for ep in all_process_endpoints():
        host, _, port = ep.rpartition(":")
        c = connect(host, int(port), timeout=3)
        try:
            fn(c)
            ok.append(ep)
        except Exception as ex:
            failed.append(ep)
            warning(f"{ep}: {type(ex).__name__}")
    return ok, failed


def sync_acl_from_peers():
    """Copy the client users from a reachable peer to the local processes
    (a member that was down missed grants and revocations)"""
    if not is_cluster():
        return False
    local = {f"{local_host()}:{p}" for p, _ in ports().values()}
    source = None
    for ep in all_process_endpoints():
        if ep in local:
            continue
        host, _, port = ep.rpartition(":")
        c = connect(host, int(port), timeout=3)
        if ping(c):
            source = c
            break
    if not source:
        return False
    wanted = client_acl_lines(source)
    for proc in PROCS:
        try:
            c = local_conn(proc)
            have = client_acl_lines(c)
            for user in have:
                if user not in wanted:
                    c.execute_command("ACL", "DELUSER", user)
            for user, line in wanted.items():
                if have.get(user) != line:
                    apply_client_line(c, line)
            save_acl(c)
        except Exception as ex:
            warning(f"ACL sync of the {proc} process failed: {type(ex).__name__}")
    return True


# ---------------------------------------------------------------------------
# Validation helper for actions
# ---------------------------------------------------------------------------

def validation_error(field, value, error, exit_code=3):
    agent = _agent()
    agent.set_status("validation-failed")
    json.dump([{"field": field, "parameter": field, "value": value, "error": error}], fp=sys.stdout)
    sys.exit(exit_code)


def caller_module():
    """Module ID of the caller, "" for administrators"""
    user = env("AGENT_TASK_USER")
    return user.removeprefix("module/") if user.startswith("module/") else ""


def resolve_client_id(data):
    caller = caller_module()
    requested = (data.get("client_id") or "").strip()
    if caller:
        if requested and requested != caller:
            validation_error("client_id", requested, "client_id_mismatch")
        return caller
    if not requested:
        validation_error("client_id", "", "required")
    if not CLIENT_ID_RE.match(requested):
        validation_error("client_id", requested, "invalid")
    return requested


def client_endpoints():
    """[{host, port, module_id, node}] for clients: every member's main
    process, or this instance"""
    if is_cluster():
        return [{"host": m["host"], "port": m["procs"]["main"][0], "module_id": m["module_id"],
                 "node": m["node"]} for m in members()]
    return [{"host": local_host(), "port": ports()["main"][0], "module_id": env("MODULE_ID"),
             "node": env("NODE_ID")}]
