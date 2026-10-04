"""
Verify the permission model:
  - a random user has level 0 and passes no admin check
  - the owner has level 100
  - port validation blocks privileged ports
"""
import io
import sys
import types

sys.stdout.reconfigure(encoding="utf-8")

# Extract just the permission logic without importing discord
src = io.open("bot.py", encoding="utf-8").read()

ns = {
    "MAIN_ADMIN_ID": 1465329430929867046,
    "PERM_LEVELS": {
        "Owner": 100, "Super Admin": 90, "VPS Manager": 70,
        "Support": 50, "Billing": 40, "Moderator": 30, "User": 0,
    },
    "admin_levels": {},
    "admin_data": {"admins": []},
    "SSH_PORT_START": 10000,
    "vps_data": {},
}

# pull the real functions out of the source
import ast
tree = ast.parse(src)
wanted = {"get_level", "level_name", "has_level", "_validate_user_port"}
wanted_assign = {
    "BLOCKED_HOST_PORTS", "PANEL_RESERVED_PORTS",
}
try:
    from core.hypervisor import BLOCKED_HOST_PORTS as _BHP
    ns["BLOCKED_HOST_PORTS"] = _BHP
except Exception:
    pass
for node in tree.body:
    if isinstance(node, ast.Assign):
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id in wanted_assign:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<t>", "exec"), ns)
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in wanted:
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<t>", "exec"), ns)

get_level = ns["get_level"]
level_name = ns["level_name"]
has_level = ns["has_level"]
validate = ns["_validate_user_port"]

OWNER = 1465329430929867046
RANDOM = 111111111111111111
ADMIN_PROMOTED = 222222222222222222

print("=== LEVEL CHECKS ===")
print(f"owner level            : {get_level(OWNER)}  (expect 100)")
print(f"random user level      : {get_level(RANDOM)}  (expect 0)")
print(f"random user name       : {level_name(RANDOM)}  (expect User)")

ns["admin_levels"][str(ADMIN_PROMOTED)] = "Super Admin"
print(f"promoted user level    : {get_level(ADMIN_PROMOTED)}  (expect 90)")
assert get_level(ADMIN_PROMOTED) == 90, "FAIL: promotion did not register"

print()
print("=== GATE CHECKS (random user must be DENIED) ===")
for gate in ("Super Admin", "VPS Manager", "Support", "Billing", "Moderator"):
    ok = has_level(RANDOM, gate)
    print(f"  random passes {gate:14}: {ok}   (expect False)")
    assert ok is False, f"FAIL: random user passes {gate}"

print()
print("=== OWNER MUST PASS ===")
for gate in ("Super Admin", "Moderator", "Billing", "Support", "VPS Manager"):
    ok = has_level(OWNER, gate)
    print(f"  owner passes {gate:14}: {ok}   (expect True)")
    assert ok is True, f"FAIL: owner denied {gate}"

print()
print("=== PORT VALIDATION ===")
cases = [
    (8080, 80, "normal web port", None),
    (22, 22, "host ssh", "reject"),
    (2375, 2375, "docker api", "reject"),
    (3306, 3306, "mysql", "reject"),
    (6379, 6379, "redis", "reject"),
    (80, 80, "privileged <1024", "reject"),
    (10000, 22, "inside reserved range", None),
    (99999, 80, "out of range", "reject"),
    (8443, 443, "normal https alt", None),
    (8000, 80, "normal web alt", None),
    (9090, 80, "panel port", "reject"),
    (27017, 27017, "mongodb", "reject"),
    (5432, 5432, "postgres", "reject"),
    (1024, 80, "lowest allowed", None),
    (1023, 80, "just under 1024", "reject"),
]
for host, cont, label, expect in cases:
    res = validate(host, cont)
    got = "reject" if res else None
    status = "OK " if got == expect else "FAIL"
    print(f"  [{status}] {host}:{cont:<6} {label:24} -> {res[:44] if res else 'allowed'}")
    assert got == expect, f"FAIL {host}:{cont} expected {expect} got {got}"

print()
print("=== SSH PORT COLLISION ===")
ns["vps_data"] = {"999": [{"ssh_port": 15000}]}
res = validate(15000, 80)
print(f"  15000 taken -> {res}")
assert res is not None, "FAIL: collision not detected"

print()
print("ALL PERMISSION TESTS PASSED")