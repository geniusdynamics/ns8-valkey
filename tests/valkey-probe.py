#
# Copyright (C) 2026 Genius Dynamics
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Test client: connect with the output of grant-client and write or read
keys. The grant (password included) comes from stdin, never from argv:

    runagent -m valkey1 python3 /tmp/valkey-probe.py write|read|ping COUNT < grant.json

Prints a JSON object without secrets.
"""

import json
import sys
import redis
from redis.cluster import RedisCluster, ClusterNode

op, count = sys.argv[1], int(sys.argv[2])
grant = json.load(sys.stdin)
mode, user, password = grant["ha_mode"], grant["username"], grant["password"]
endpoints = [(n["host"], int(n["port"])) for n in grant["nodes"]]
result = {"ok": False}
try:
    if mode == "cluster":
        client = RedisCluster(startup_nodes=[ClusterNode(h, p) for h, p in endpoints],
                              username=user, password=password, decode_responses=True,
                              socket_timeout=5, socket_connect_timeout=5)
    else:
        client = redis.Redis(host=endpoints[0][0], port=endpoints[0][1], username=user,
                             password=password, decode_responses=True,
                             socket_timeout=5, socket_connect_timeout=5)
    if op == "ping":
        result["ok"] = bool(client.ping())
    elif op == "write":
        for i in range(count):
            client.set(f"probe:{i}", str(i))
        result["ok"] = True
    elif op == "read":
        good = sum(1 for i in range(count) if client.get(f"probe:{i}") == str(i))
        result["ok"] = good == count
        result["good"] = good
except Exception as ex:
    result["error"] = type(ex).__name__
print(json.dumps(result))
